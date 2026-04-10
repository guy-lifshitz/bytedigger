#!/usr/bin/env bats
# RED tests for scripts/learning-store.sh
# All tests MUST fail until learning-store.sh is implemented.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/learning-store.sh"
PLUGIN_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR

  # Default bytedigger.json with file backend
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true,
  "learning": {
    "backend": "file",
    "max_inject": 10,
    "max_stored": 200,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF

  # Point script at TMPDIR as CWD surrogate
  export BYTEDIGGER_CONFIG="$TMPDIR/bytedigger.json"

  # Create learnings dir
  mkdir -p "$TMPDIR/.bytedigger/learnings"

  # Create a scratchpad dir structure (for extract tests)
  mkdir -p "$TMPDIR/.hal-build/reviews"
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# Helper: write N entries to a category file
# ---------------------------------------------------------------------------
_write_entries() {
  local file="$1"
  local count="$2"
  > "$file"
  for i in $(seq 1 "$count"); do
    echo "- Entry number $i with some lesson text" >> "$file"
    echo "  <!-- tags: entry,lesson,text | source: forge-test-$i | date: 2026-01-01 -->" >> "$file"
  done
}

# ---------------------------------------------------------------------------
# Test 1: inject with backend=none → exit 0, empty stdout
# ---------------------------------------------------------------------------
@test "T01_inject_backend_none_exits_0_empty_stdout" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "learning": {
    "backend": "none",
    "max_inject": 10,
    "max_stored": 200,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF
  run bash "$SCRIPT" inject "architecture service" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

# ---------------------------------------------------------------------------
# Test 2: inject with no learnings files → exit 0, stdout empty, count 0
# ---------------------------------------------------------------------------
@test "T02_inject_no_learnings_files_exits_0_count_0" {
  # learnings dir is empty (setup creates it but no .md files)
  run bash "$SCRIPT" inject "architecture" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # stdout should be empty (no entries to inject)
  [ -z "$output" ]
  # build-state.yaml in TMPDIR should have learnings_injected: 0
  grep -q "learnings_injected: 0" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# Test 3: inject with 3 matching entries and max_inject=2 → exactly 2 entries in stdout
# ---------------------------------------------------------------------------
@test "T03_inject_max_inject_caps_results" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "learning": {
    "backend": "file",
    "max_inject": 2,
    "max_stored": 200,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF
  # Write 3 matching entries to architecture.md
  cat > "$TMPDIR/.bytedigger/learnings/architecture.md" <<'EOF'
- Service layer wraps all database calls for isolation
  <!-- tags: service,layer,wraps,database,calls | source: forge-001 | date: 2026-04-01 -->
- Repository pattern isolates database access from service layer
  <!-- tags: repository,pattern,isolates,database,access | source: forge-002 | date: 2026-04-02 -->
- Domain service should not call database directly
  <!-- tags: domain,service,should,call,database | source: forge-003 | date: 2026-04-03 -->
EOF
  run bash "$SCRIPT" inject "service database" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # Count the number of bullet-point entry lines in stdout
  entry_count=$(echo "$output" | grep -c "^- " || true)
  [ "$entry_count" -eq 2 ]
}

# ---------------------------------------------------------------------------
# Test 4: inject keyword matching is case-insensitive
# ---------------------------------------------------------------------------
@test "T04_inject_case_insensitive_keyword_match" {
  cat > "$TMPDIR/.bytedigger/learnings/architecture.md" <<'EOF'
- Service layer should wrap all Database calls
  <!-- tags: service,layer,database,calls | source: forge-001 | date: 2026-04-01 -->
EOF
  run bash "$SCRIPT" inject "database" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # Should match even though stored entry has "Database" (uppercase)
  echo "$output" | grep -qi "database"
}

# ---------------------------------------------------------------------------
# Test 5: inject matches tags in HTML comments
# ---------------------------------------------------------------------------
@test "T05_inject_matches_tags_in_html_comments" {
  cat > "$TMPDIR/.bytedigger/learnings/api.md" <<'EOF'
- Always check response codes before parsing body
  <!-- tags: api,handler,response,codes | source: forge-001 | date: 2026-04-01 -->
EOF
  # Search for "handler" which only appears in the tag comment, not the lesson text
  run bash "$SCRIPT" inject "handler" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # The entry should appear in output (matched via tag)
  echo "$output" | grep -q "response codes"
}

# ---------------------------------------------------------------------------
# Test 6: extract with backend=none → exit 0, no files modified
# ---------------------------------------------------------------------------
@test "T06_extract_backend_none_exits_0_no_files" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "learning": {
    "backend": "none",
    "max_inject": 10,
    "max_stored": 200,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF
  # Write a learnings-raw.md to confirm it won't be read
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- Service layer should wrap all DB calls
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # No category file should have been created
  [ ! -f "$TMPDIR/.bytedigger/learnings/architecture.md" ]
}

# ---------------------------------------------------------------------------
# Test 7: extract with valid learnings-raw.md (2 entries) → 2 entries appended
# ---------------------------------------------------------------------------
@test "T07_extract_valid_raw_md_two_entries_appended" {
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- Service layer should wrap all DB calls
- [bug-fix] --- Always validate input before DB insert
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # architecture.md must exist and contain the lesson
  [ -f "$TMPDIR/.bytedigger/learnings/architecture.md" ]
  grep -q "Service layer should wrap all DB calls" "$TMPDIR/.bytedigger/learnings/architecture.md"
  # bug-fix.md must exist and contain the lesson
  [ -f "$TMPDIR/.bytedigger/learnings/bug-fix.md" ]
  grep -q "validate input before DB insert" "$TMPDIR/.bytedigger/learnings/bug-fix.md"
  # build-state.yaml must report 2 extracted
  grep -q "learnings_extracted: 2" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# Test 8: extract generates correct tag format (lowercase, >3 chars, comma-separated)
# ---------------------------------------------------------------------------
@test "T08_extract_generates_correct_tag_format" {
  # Use a lesson with known short words (<=3 chars) that must be excluded:
  # "DB" (2), "all" (3), "the" (3), "to" (2) should NOT appear in tags
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [code-quality] --- Use DB to wrap all the nested conditions early
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/.bytedigger/learnings/code-quality.md" ]
  # Tags comment must exist with lowercase comma-separated words longer than 3 chars
  grep -q "<!-- tags:" "$TMPDIR/.bytedigger/learnings/code-quality.md"
  # All tags must be lowercase
  tag_line=$(grep "<!-- tags:" "$TMPDIR/.bytedigger/learnings/code-quality.md")
  tags_section=$(echo "$tag_line" | sed 's/.*tags: \([^|]*\).*/\1/')
  # No uppercase letters in tags
  echo "$tags_section" | grep -qvE '[A-Z]'
  # Words >3 chars must be present (e.g., "wrap", "nested", "conditions", "early")
  echo "$tags_section" | grep -q "wrap"
  echo "$tags_section" | grep -q "nested"
  # Short words (<=3 chars) must NOT appear as tags
  # Split tags by comma and check each one is >3 chars
  IFS=',' read -ra tag_array <<< "$(echo "$tags_section" | tr -d ' ')"
  for tag in "${tag_array[@]}"; do
    tag=$(echo "$tag" | tr -d '[:space:]')
    [ -z "$tag" ] && continue
    [ "${#tag}" -gt 3 ]
  done
}

# ---------------------------------------------------------------------------
# Test 9: extract adds source and date metadata
# ---------------------------------------------------------------------------
@test "T09_extract_adds_source_and_date_metadata" {
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- Separate concerns between layers
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/.bytedigger/learnings/architecture.md" ]
  # Must have source field matching forge-* pattern
  grep -q "source: forge-" "$TMPDIR/.bytedigger/learnings/architecture.md"
  # Must have date field in YYYY-MM-DD format
  grep -qE "date: [0-9]{4}-[0-9]{2}-[0-9]{2}" "$TMPDIR/.bytedigger/learnings/architecture.md"
}

# ---------------------------------------------------------------------------
# Test 10: extract trims oldest when > max_stored
# ---------------------------------------------------------------------------
@test "T10_extract_trims_oldest_when_over_max_stored" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "learning": {
    "backend": "file",
    "max_inject": 10,
    "max_stored": 5,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF
  # Pre-populate architecture.md with exactly max_stored (5) entries (each entry = 2 lines)
  _write_entries "$TMPDIR/.bytedigger/learnings/architecture.md" 5
  # Add one more via extract
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- New entry that should push oldest out
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # Count bullet entries (lines starting with "- ")
  entry_count=$(grep -c "^- " "$TMPDIR/.bytedigger/learnings/architecture.md" || true)
  [ "$entry_count" -eq 5 ]
  # The new entry should be present (it replaced the oldest)
  grep -q "New entry that should push oldest out" "$TMPDIR/.bytedigger/learnings/architecture.md"
  # Entry 1 (oldest) should be gone
  ! grep -q "Entry number 1 with" "$TMPDIR/.bytedigger/learnings/architecture.md"
}

# ---------------------------------------------------------------------------
# Test 11: extract with missing learnings-raw.md → exit 0, learnings_extracted: 0
# ---------------------------------------------------------------------------
@test "T11_extract_missing_raw_md_exits_0_count_0" {
  # No learnings-raw.md in scratchpad
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  grep -q "learnings_extracted: 0" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# Test 12: extract skips malformed lines
# ---------------------------------------------------------------------------
@test "T12_extract_skips_malformed_lines" {
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- Good entry that should be stored
- This line has no category prefix and should be skipped
- [bug-fix] --- Another valid entry
- [MALFORMED without closing bracket --- should be skipped
- just plain text, no structure
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # Only 2 valid entries
  grep -q "learnings_extracted: 2" "$TMPDIR/build-state.yaml"
  [ -f "$TMPDIR/.bytedigger/learnings/architecture.md" ]
  [ -f "$TMPDIR/.bytedigger/learnings/bug-fix.md" ]
}

# ---------------------------------------------------------------------------
# Test 13: extract sanitizes category to safe filename
# ---------------------------------------------------------------------------
@test "T13_extract_sanitizes_category_to_safe_filename" {
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [Code Quality] --- Use early returns to reduce nesting
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # "Code Quality" → "code-quality.md" (lowercase, spaces to dashes)
  [ -f "$TMPDIR/.bytedigger/learnings/code-quality.md" ]
  # Original filename with spaces must NOT exist
  [ ! -f "$TMPDIR/.bytedigger/learnings/Code Quality.md" ]
}

# ---------------------------------------------------------------------------
# Test 14: config missing "learning" key → behaves as backend=none
# ---------------------------------------------------------------------------
@test "T14_missing_learning_config_key_treats_as_none" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true
}
EOF
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- This should not be stored when backend is none
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # No learnings directory should be modified
  [ ! -f "$TMPDIR/.bytedigger/learnings/architecture.md" ]
}

# ---------------------------------------------------------------------------
# Test 15: inject with backend=sqlite → execs or falls back gracefully
# ---------------------------------------------------------------------------
@test "T15_inject_backend_sqlite_delegates_or_falls_back" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "learning": {
    "backend": "sqlite",
    "max_inject": 10,
    "max_stored": 200,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF
  # learning-store-sqlite.sh does not exist → should fall back to none and exit 0
  run bash "$SCRIPT" inject "service architecture" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
}

# ===========================================================================
# Integration Tests (16-20)
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 16: Phase 0 with backend=file creates prior-learnings.md in scratchpad
# ---------------------------------------------------------------------------
@test "T16_phase0_backend_file_creates_prior_learnings" {
  # Pre-populate a learning entry
  cat > "$TMPDIR/.bytedigger/learnings/architecture.md" <<'EOF'
- Service layer wraps database calls
  <!-- tags: service,layer,wraps,database,calls | source: forge-001 | date: 2026-04-01 -->
EOF

  # Simulate Phase 0: run inject and redirect stdout to prior-learnings.md
  mkdir -p "$TMPDIR/.hal-build/research"
  bash "$SCRIPT" inject "service database" --config "$TMPDIR/bytedigger.json" \
    > "$TMPDIR/.hal-build/research/prior-learnings.md"
  local exit_code=$?
  [ "$exit_code" -eq 0 ]
  [ -f "$TMPDIR/.hal-build/research/prior-learnings.md" ]
  grep -q "Service layer wraps database calls" "$TMPDIR/.hal-build/research/prior-learnings.md"
}

# ---------------------------------------------------------------------------
# Test 17: Phase 7 with backend=file persists learnings to .bytedigger/learnings/
# ---------------------------------------------------------------------------
@test "T17_phase7_backend_file_persists_learnings" {
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [performance] --- Cache expensive queries with TTL of 5 minutes
- [workflow] --- Always run tests before committing
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  [ -f "$TMPDIR/.bytedigger/learnings/performance.md" ]
  grep -q "Cache expensive queries" "$TMPDIR/.bytedigger/learnings/performance.md"
  [ -f "$TMPDIR/.bytedigger/learnings/workflow.md" ]
  grep -q "Always run tests before committing" "$TMPDIR/.bytedigger/learnings/workflow.md"
}

# ---------------------------------------------------------------------------
# Test 18: Full cycle: extract in build N, inject sees it in build N+1
# ---------------------------------------------------------------------------
@test "T18_full_cycle_extract_then_inject" {
  # Build N: extract a learning
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- Always use dependency injection for testability
EOF
  bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ -f "$TMPDIR/.bytedigger/learnings/architecture.md" ]

  # Build N+1: inject should find it
  run bash "$SCRIPT" inject "dependency injection testability" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "dependency injection"
}

# ---------------------------------------------------------------------------
# Test 19: backend=none has zero behavioral change — no dirs created, only skip_reason field
# ---------------------------------------------------------------------------
@test "T19_backend_none_zero_behavioral_change" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "learning": {
    "backend": "none",
    "max_inject": 10,
    "max_stored": 200,
    "storage_path": ".bytedigger/learnings"
  }
}
EOF
  # Remove learnings dir to confirm it won't be created
  rm -rf "$TMPDIR/.bytedigger/learnings"

  run bash "$SCRIPT" inject "anything" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # No learnings directory created
  [ ! -d "$TMPDIR/.bytedigger/learnings" ]
  # build-state.yaml must have learning_skip_reason: disabled
  grep -q "learning_skip_reason: disabled" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# Test 20: Learning failure does not block pipeline (corrupt file → exit 0)
# ---------------------------------------------------------------------------
@test "T20_corrupt_learnings_file_exits_0_graceful_degradation" {
  # Write a corrupt/binary-ish file as a learning store
  printf '\x00\x01\x02\x03corrupt\xff\xfe' > "$TMPDIR/.bytedigger/learnings/architecture.md"

  # inject should not crash even with corrupt file
  run bash "$SCRIPT" inject "architecture service" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # State file must be written (pipeline can read learning_backend)
  grep -q "learning_backend: file" "$TMPDIR/build-state.yaml"

  # Extract also must not crash
  cat > "$TMPDIR/.hal-build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [architecture] --- New entry despite corrupt existing file
EOF
  run bash "$SCRIPT" extract "$TMPDIR/.hal-build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]
  # Extraction count must be recorded (even if 0 or 1)
  grep -q "learnings_extracted:" "$TMPDIR/build-state.yaml"
}
