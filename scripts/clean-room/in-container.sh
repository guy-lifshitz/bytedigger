#!/usr/bin/env bash
# Runs INSIDE a stock container against a committed-tree-only copy of the repo
# (see run.sh: `git archive HEAD` piped into `docker run`). Nothing here may
# assume anything the base image does not ship.
#
# Modes:
#   quickstart  README Quickstart verbatim + keyless demo + suite COLLECTION.
#               Zero host tools installed. This is the outsider's actual path.
#   suite       Installs the suite's undeclared host dependencies (bd#102),
#               then runs the full test suite.
#
# Why two modes: see docs/clean-room.md §Decision. `quickstart` proves the
# product works on a bare image; `suite` proves the tests pass and pins the
# exact tool debt in one deletable block.
set -euo pipefail

MODE="${1:?usage: in-container.sh <quickstart|suite>}"
ROOT=/work

step() { printf '\n=== %s\n' "$*"; }

# --- AC2: the tree really is committed-only -------------------------------
step "clean-room preconditions"
[ -d "$ROOT/engine_py" ] || { echo "FAIL: tree did not extract"; exit 1; }
if [ -e "$ROOT/.git" ]; then
  echo "FAIL: .git present — this is not a git-archive tree"; exit 1
fi
echo "ok: committed tree only, no .git ($(find "$ROOT" -maxdepth 1 | wc -l | tr -d ' ') top-level entries)"
echo "python: $(python3 -V 2>&1)"

case "$MODE" in
  quickstart)
    # Deliberately NO apt-get here. If a README step needs a host tool, this
    # job must go red — that is the whole point of the job.
    step "README parity guard (AC5)"
    python3 "$ROOT/scripts/clean-room/readme_parity.py" "$ROOT"

    step "README Quickstart, verbatim"
    cd "$ROOT/engine_py"
    # shellcheck disable=SC1091
    python3 -m venv .venv && source .venv/bin/activate
    pip install -U pip
    pip install -e .
    bytedigger-engine --workflow echo --ctx-json '{"question":"hello"}' --event-log /tmp/events.jsonl
    bytedigger-engine --derive-state /tmp/events.jsonl
    python3 ../examples/verified-tdd-run/run_demo.py

    step "README promise: the doctor command it tells you to run"
    bytedigger-engine doctor

    step "test suite COLLECTS on a bare image (the bd#97 gate)"
    # Execution needs host tools the bare image lacks (bd#102); collection does
    # not, and collection is exactly what bd#97 broke. Requiring 0 collection
    # errors here is honest and blocking.
    pip install -e ".[test]"
    python3 -m pytest tests/ -q -p no:cacheprovider --collect-only >/tmp/collect.log 2>&1 || {
      echo "FAIL: collection errored"; tail -40 /tmp/collect.log; exit 1;
    }
    tail -1 /tmp/collect.log
    if grep -qE '^(ERROR|!!!.*error)' /tmp/collect.log; then
      echo "FAIL: collection reported errors"; grep -E '^ERROR' /tmp/collect.log | head -20; exit 1
    fi
    echo "ok: suite collects clean"
    ;;

  suite)
    # ---------------------------------------------------------------------
    # bd#102 CLOSURE SET. The engine test suite assumes these are on PATH.
    # None of them is declared anywhere a user would read.
    #   git      — 90+ test modules shell out to git
    #   zsh      — subprocess seam tests
    #   bun      — ts/js test-group tests (phase_5 red/green telemetry)
    #   semgrep  — red-lint preflight; the engine calls it "build-critical"
    #   git identity — tests that commit
    # CLOSING bd#102 MEANS DELETING THIS BLOCK. Nothing else in this file
    # changes. CI then proves the suite runs on a bare image.
    # ---------------------------------------------------------------------
    step "bd#102: install the suite's undeclared host dependencies"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends git zsh curl unzip ca-certificates
    export BUN_INSTALL=/usr/local/bun
    curl -fsSL https://bun.sh/install | bash
    export PATH="$BUN_INSTALL/bin:$PATH"
    git config --global user.email "clean-room@ci.invalid"
    git config --global user.name "clean-room ci"
    git config --global init.defaultBranch main
    echo "git=$(command -v git) zsh=$(command -v zsh) bun=$(bun --version)"

    step "install engine + test + security extras"
    cd "$ROOT/engine_py"
    # shellcheck disable=SC1091
    python3 -m venv .venv && source .venv/bin/activate
    pip install -U pip
    # [security] carries semgrep — part of the bd#102 closure set above.
    pip install -e ".[test,security]"
    echo "semgrep=$(command -v semgrep)"

    step "full test suite"
    python3 -m pytest tests/ -q -p no:cacheprovider --timeout=180
    ;;

  *)
    echo "unknown mode: $MODE"; exit 2 ;;
esac

step "clean-room $MODE: PASS"
