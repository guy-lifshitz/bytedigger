#!/usr/bin/env bats
# RED tests for scripts/learning-store-sqlite.sh (SQLite backend delegate)
# Phase 5.1 RED — ALL TESTS MUST FAIL until learning-store-sqlite.sh is created.
#
# Mirrors conventions from tests/learning-store.bats exactly:
#   - setup()/teardown() with mktemp TMPDIR
#   - BYTEDIGGER_CONFIG env var
#   - run + $status + $output assertions
#   - T-prefix naming

SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts"
DELEGATE="$SCRIPT_DIR/learning-store-sqlite.sh"
DISPATCHER="$SCRIPT_DIR/learning-store.sh"

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR
  export LEARNING_STORE_STRICT=1

  # SQLite bytedigger.json
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{ "learning": { "backend": "sqlite", "max_inject": 10, "max_stored": 200 } }
EOF
  export BYTEDIGGER_CONFIG="$TMPDIR/bytedigger.json"

  # Create DB with correct schema (fixture created by Phase 5 RED)
  DB="$TMPDIR/learnings.db"
  sqlite3 "$DB" < "$(dirname "$BATS_TEST_FILENAME")/fixtures/learning-schema.sql"
  export LEARNING_DB_URL="$DB"

  # Scratchpad dir structure (matches dispatcher convention)
  mkdir -p "$TMPDIR/.build/reviews"
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# T1: sqlite backend + valid LEARNING_DB_URL + extract → row inserted
# ---------------------------------------------------------------------------
@test "T1: sqlite backend + valid LEARNING_DB_URL + extract → row inserted" {
  # Place a valid learnings-raw.md fixture in the scratchpad reviews dir
  cat > "$TMPDIR/.build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [testing] --- write tests before implementation to verify desired behaviour
EOF

  run bash "$DELEGATE" extract "$TMPDIR/.build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]

  # Exactly one row should be in the DB (one bullet in the fixture)
  count=$(sqlite3 "$LEARNING_DB_URL" "SELECT COUNT(*) FROM learning_entries WHERE source='bytedigger';")
  [ "$count" -eq 1 ]

  # The inserted pattern must be "testing" (sanitized from "[testing]")
  pattern=$(sqlite3 "$LEARNING_DB_URL" "SELECT pattern FROM learning_entries WHERE source='bytedigger' LIMIT 1;")
  [ "$pattern" = "testing" ]

  # State file must record the extraction count
  grep -q "learnings_extracted:" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# T2: sqlite backend + unset LEARNING_DB_URL + LEARNING_STORE_STRICT=1 → exit non-zero + stderr
# ---------------------------------------------------------------------------
@test "T2: sqlite backend + unset LEARNING_DB_URL + LEARNING_STORE_STRICT=1 → exit non-zero + stderr" {
  unset LEARNING_DB_URL
  export LEARNING_STORE_STRICT=1

  run bash "$DELEGATE" extract "$TMPDIR/.build" --config "$TMPDIR/bytedigger.json"

  # Must exit non-zero under STRICT mode
  [ "$status" -ne 0 ]

  # stderr must mention LEARNING_DB_URL
  echo "$output" | grep -q "LEARNING_DB_URL"
}

# ---------------------------------------------------------------------------
# T3: sqlite backend + unset LEARNING_DB_URL + no STRICT → exit 0 + state-file error key
# ---------------------------------------------------------------------------
@test "T3: sqlite backend + unset LEARNING_DB_URL + no STRICT → exit 0 + state-file error key" {
  unset LEARNING_DB_URL
  unset LEARNING_STORE_STRICT

  run bash "$DELEGATE" extract "$TMPDIR/.build" --config "$TMPDIR/bytedigger.json"

  # Must exit 0 in non-strict (pipeline) mode
  [ "$status" -eq 0 ]

  # State file must have the exact slug (spec §6 row 1 — external contract)
  grep -qE 'learnings_extraction_error:.*LEARNING_DB_URL_not_set' "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# T4: backend=none → no-op (existing behavior preserved via dispatcher)
# ---------------------------------------------------------------------------
@test "T4: backend=none → no-op" {
  # Override config to use backend=none
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{ "learning": { "backend": "none", "max_inject": 10, "max_stored": 200 } }
EOF
  export BYTEDIGGER_CONFIG="$TMPDIR/bytedigger.json"

  # Unset STRICT so we test the dispatcher's none path (not strict errors)
  unset LEARNING_STORE_STRICT

  run bash "$DISPATCHER" inject "keywords" --config "$TMPDIR/bytedigger.json"

  # Must exit 0
  [ "$status" -eq 0 ]

  # stdout must be empty (no learnings emitted for none backend)
  [ -z "$output" ]

  # DB must be untouched (0 rows)
  count=$(sqlite3 "$LEARNING_DB_URL" "SELECT COUNT(*) FROM learning_entries;" 2>/dev/null || echo "0")
  [ "$count" -eq 0 ]

  # State file should record skip_reason: disabled
  grep -q "learning_skip_reason: disabled" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# T5: sqlite backend + inject → DB rows emitted to stdout in file-backend bullet format
# ---------------------------------------------------------------------------
@test "T5: sqlite backend + inject → DB rows emitted to stdout in file-backend bullet format" {
  # Seed DB with a known row
  NOW_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
  ROW_ID=$(openssl rand -hex 16)
  sqlite3 "$LEARNING_DB_URL" <<SQL
INSERT INTO learning_entries
  (id, pattern, approach, domain, confidence, attempts, successes, failures,
   partial_successes, created_at, first_used, last_used, source)
VALUES (
  '${ROW_ID}',
  'shell',
  'always quote variables to avoid word splitting',
  'shell',
  1.0, 1, 1, 0, 0,
  ${NOW_MS}, ${NOW_MS}, ${NOW_MS},
  'bytedigger'
);
SQL

  run bash "$DELEGATE" inject "shell" --config "$TMPDIR/bytedigger.json"

  [ "$status" -eq 0 ]

  # stdout must contain at least one bullet matching the expected format:
  # ^- [<pattern>] --- <approach>$
  echo "$output" | grep -qE '^- \[[^]]+\] --- .+'

  # State file must record learnings_injected
  grep -q "learnings_injected:" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# T6: sqlite backend + extract + duplicate (pattern, approach) → INSERT OR IGNORE no-op
# ---------------------------------------------------------------------------
@test "T6: sqlite backend + extract + duplicate (pattern, approach) → INSERT OR IGNORE no-op" {
  # Pre-seed DB with the exact entry that the raw.md will attempt to insert
  NOW_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
  ROW_ID=$(openssl rand -hex 16)
  sqlite3 "$LEARNING_DB_URL" <<SQL
INSERT INTO learning_entries
  (id, pattern, approach, domain, confidence, attempts, successes, failures,
   partial_successes, created_at, first_used, last_used, source)
VALUES (
  '${ROW_ID}',
  'testing',
  'write tests first',
  'testing',
  1.0, 1, 1, 0, 0,
  ${NOW_MS}, ${NOW_MS}, ${NOW_MS},
  'bytedigger'
);
SQL

  # Write the same learning to learnings-raw.md
  cat > "$TMPDIR/.build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [testing] --- write tests first
EOF

  run bash "$DELEGATE" extract "$TMPDIR/.build" --config "$TMPDIR/bytedigger.json"

  [ "$status" -eq 0 ]

  # Row count must still be exactly 1 (INSERT OR IGNORE no-op on duplicate)
  count=$(sqlite3 "$LEARNING_DB_URL" \
    "SELECT COUNT(*) FROM learning_entries WHERE pattern='testing' AND approach='write tests first';")
  [ "$count" -eq 1 ]

  # delegate uses newly-inserted semantics (changes()>0 only) — duplicate = 0 new rows
  grep -q "learnings_extracted: 0" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# T7 (MANDATORY — ship condition 3): LEARNING_DB_URL with '..' path traversal → rejected
# ---------------------------------------------------------------------------
@test "T7: LEARNING_DB_URL containing '..' path traversal → rejected" {
  export LEARNING_DB_URL="../../etc/passwd"
  export LEARNING_STORE_STRICT=1

  run bash "$DELEGATE" extract "$TMPDIR/.build" --config "$TMPDIR/bytedigger.json"

  # Must exit non-zero under STRICT mode for path traversal
  [ "$status" -ne 0 ]

  # stderr must mention path rejection
  echo "$output" | grep -qiE "invalid path|path.*reject|traversal|\.\."

  # State file must record the exact slug (spec §6 row 2 — external contract)
  grep -qE 'learnings_extraction_error:.*invalid_db_path' "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# T8: apostrophe in lesson text → row inserted (quote-safety)
# Confirms sql_escape() handles single-quotes without silent-drop
# ---------------------------------------------------------------------------
@test "T8: extract with apostrophe in lesson text → row inserted (quote-safety)" {
  cat > "$TMPDIR/.build/reviews/learnings-raw.md" <<'EOF'
## New Learnings

- [quote-test] --- don't break on apostrophes; it's a footgun
EOF

  run bash "$DELEGATE" extract "$TMPDIR/.build" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]

  # Row must be in DB (apostrophe did not cause silent-drop)
  count=$(sqlite3 "$LEARNING_DB_URL" "SELECT COUNT(*) FROM learning_entries WHERE pattern='quote-test';")
  [ "$count" -eq 1 ]

  # Approach text must be preserved verbatim (apostrophes intact)
  approach=$(sqlite3 "$LEARNING_DB_URL" "SELECT approach FROM learning_entries WHERE pattern='quote-test';")
  [ "$approach" = "don't break on apostrophes; it's a footgun" ]
}

# ---------------------------------------------------------------------------
# T9: learning_backend: sqlite written on both non-empty and empty-keywords inject
# Confirms spec §5 state table row "Successful inject → learning_backend: sqlite"
# ---------------------------------------------------------------------------
@test "T9: learning_backend: sqlite written to state file on inject" {
  # Seed DB with a row for the non-empty keywords path
  NOW_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
  ROW_ID=$(openssl rand -hex 16)
  sqlite3 "$LEARNING_DB_URL" <<SQL
INSERT INTO learning_entries
  (id, pattern, approach, domain, confidence, attempts, successes, failures,
   partial_successes, created_at, first_used, last_used, source)
VALUES (
  '${ROW_ID}',
  'backend-test',
  'always write learning_backend to state',
  'backend-test',
  1.0, 1, 1, 0, 0,
  ${NOW_MS}, ${NOW_MS}, ${NOW_MS},
  'bytedigger'
);
SQL

  run bash "$DELEGATE" inject "backend-test" --config "$TMPDIR/bytedigger.json"
  [ "$status" -eq 0 ]

  # State file must record learning_backend: sqlite (spec §5)
  grep -qE '^learning_backend:\s*sqlite' "$TMPDIR/build-state.yaml"
}
