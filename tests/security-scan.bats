#!/usr/bin/env bats
# RED tests for scripts/security-scan.sh — security pattern scanner
# All tests MUST fail until security-scan.sh is implemented.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/security-scan.sh"

# ---------------------------------------------------------------------------
# setup / teardown
# ---------------------------------------------------------------------------

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR

  # Default build-state.yaml
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "Add user authentication"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5"
forge_run_id: forge-test-001
files_modified:
  - src/auth.ts
EOF
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# Test 1: auth patterns → HIGH + AUTH
# ---------------------------------------------------------------------------

@test "T01_classifies_auth_as_high" {
  cat > "$TMPDIR/auth.ts" <<'EOF'
const payload = jwt.verify(token, secret);
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/auth.ts" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "HIGH"
  echo "$output" | grep -qi "AUTH"
}

# ---------------------------------------------------------------------------
# Test 2: crypto patterns → HIGH + CRYPTO
# ---------------------------------------------------------------------------

@test "T02_classifies_crypto_as_high" {
  cat > "$TMPDIR/crypto.ts" <<'EOF'
const ciphertext = encrypt(data, key);
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/crypto.ts" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "HIGH"
  echo "$output" | grep -qi "CRYPTO"
}

# ---------------------------------------------------------------------------
# Test 3: secrets patterns → HIGH + SECRETS
# ---------------------------------------------------------------------------

@test "T03_classifies_secrets_as_high" {
  cat > "$TMPDIR/config.ts" <<'EOF'
const api_key = process.env.SECRET;
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/config.ts" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "HIGH"
  echo "$output" | grep -qi "SECRETS"
}

# ---------------------------------------------------------------------------
# Test 4: data access only (no auth/crypto) → MEDIUM + DATA
# ---------------------------------------------------------------------------

@test "T04_classifies_data_only_as_medium" {
  cat > "$TMPDIR/api.ts" <<'EOF'
const users = fetch('/api/users');
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/api.ts" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "MEDIUM"
  echo "$output" | grep -qi "DATA"
}

# ---------------------------------------------------------------------------
# Test 5: infra-only file → LOW
# ---------------------------------------------------------------------------

@test "T05_classifies_infra_only_as_low" {
  cat > "$TMPDIR/Dockerfile" <<'EOF'
FROM node:18
WORKDIR /app
COPY . .
RUN npm install
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/Dockerfile" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "LOW"
}

# ---------------------------------------------------------------------------
# Test 6: no known patterns → LOW
# ---------------------------------------------------------------------------

@test "T06_classifies_no_patterns_as_low" {
  cat > "$TMPDIR/logger.ts" <<'EOF'
console.log('hello world');
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/logger.ts" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "LOW"
}

# ---------------------------------------------------------------------------
# Test 7: writes security_classification and security_patterns_found to state file
# ---------------------------------------------------------------------------

@test "T07_writes_to_state_file" {
  cat > "$TMPDIR/auth.ts" <<'EOF'
const payload = jwt.verify(token, secret);
EOF

  run bash "$SCRIPT" --cwd "$TMPDIR" --files "$TMPDIR/auth.ts" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  grep -q "security_classification:" "$TMPDIR/build-state.yaml"
  grep -q "security_patterns_found:" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# Test 8: empty file list → LOW + exit 0
# ---------------------------------------------------------------------------

@test "T08_handles_empty_file_list" {
  run bash "$SCRIPT" --cwd "$TMPDIR" --files "" --state-file "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qi "LOW"
}
