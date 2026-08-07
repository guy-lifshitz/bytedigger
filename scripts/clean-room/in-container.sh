#!/usr/bin/env bash
# Runs INSIDE a stock container against a committed-tree-only copy of the repo
# (see run.sh: `git archive` piped into `docker run`). Nothing here may assume
# anything the base image does not ship.
#
# Modes:
#   quickstart          README Quickstart verbatim + keyless demo + doctor
#                       + suite COLLECTION. Zero host tools installed.
#   suite               Installs bd#102's closure set, runs the full suite.
#   expect-fail:bd102   Bare-image full suite; MUST fail. Goes red when it
#                       starts passing — i.e. when bd#102 became closable.
#   expect-fail:py39    Bare-image collection on the declared minimum Python;
#                       MUST fail. Goes red when 3.9 starts working.
#
# The expect-fail modes are what make "delete the block" an enforced exit
# criterion rather than a comment nobody re-reads. See docs/clean-room.md.
set -euo pipefail

MODE="${1:?usage: in-container.sh <quickstart|suite|expect-fail:bd102|expect-fail:py39>}"
ROOT=/work
TOOLS_MANIFEST="$ROOT/scripts/clean-room/bd102-tools.manifest"

step() { printf '\n=== %s\n' "$*"; }
die()  { echo "FAIL: $*" >&2; exit 1; }

# --- the tree really is the committed tree, and all of it ------------------
step "clean-room preconditions"
[ -d "$ROOT/engine_py" ] || die "tree did not extract"
if [ -e "$ROOT/.git" ]; then die ".git present — this is not a git-archive tree"; fi
# Stronger than "no .git": the extracted file count must equal the tracked file
# count computed outside the container, so a bind mount or a `cp -r` minus .git
# cannot masquerade as a clean room.
actual_files="$(find "$ROOT" ! -type d | wc -l | tr -d ' ')"
if [ -n "${EXPECT_FILES:-}" ]; then
  [ "$actual_files" = "$EXPECT_FILES" ] || die "tree has $actual_files files, git tracks $EXPECT_FILES"
  echo "ok: committed tree only — $actual_files files, matches git ls-tree"
else
  echo "ok: committed tree only — $actual_files files (no EXPECT_FILES to compare)"
fi
echo "python: $(python3 -V 2>&1)"

install_bd102_tools() {
  # Installs exactly the set in bd102-tools.manifest. Note the shapes differ:
  # git/zsh are apt packages, bun is a curl|bash installer (it is not in the
  # Debian repos), semgrep rides the [security] pip extra.
  step "bd#102: install the suite's undeclared host dependencies"
  local tools
  tools="$(grep -v '^[[:space:]]*#' "$TOOLS_MANIFEST" | grep -v '^[[:space:]]*$' | cut -f1)"
  echo "closure set: $(echo "$tools" | tr '\n' ' ')"

  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  # curl/unzip/ca-certificates are not part of the closure set — they exist
  # only to fetch bun, which Debian does not package.
  apt-get install -y -qq --no-install-recommends git zsh curl unzip ca-certificates
  export BUN_INSTALL=/usr/local/bun
  curl -fsSL https://bun.sh/install | bash
  export PATH="$BUN_INSTALL/bin:$PATH"
  git config --global user.email "clean-room@ci.invalid"
  git config --global user.name "clean-room ci"
  git config --global init.defaultBranch main
}

assert_closure_set_on_path() {
  # "exactly the documented set" as a mechanical check: every manifest entry
  # must be on PATH, and must be named in the docs.
  local tool missing=0
  while IFS=$'\t' read -r tool _; do
    case "$tool" in ''|\#*) continue ;; esac
    if command -v "$tool" >/dev/null 2>&1; then
      echo "  ok  $tool -> $(command -v "$tool")"
    else
      echo "  MISSING $tool"; missing=1
    fi
    grep -q -- "$tool" "$ROOT/docs/clean-room.md" || {
      echo "  UNDOCUMENTED $tool (not named in docs/clean-room.md)"; missing=1; }
  done < "$TOOLS_MANIFEST"
  [ "$missing" = 0 ] || die "bd#102 closure set is not satisfied/documented"
}

setup_venv() {
  cd "$ROOT/engine_py"
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
}

case "$MODE" in
  quickstart)
    # Deliberately NO apt-get here. If a README step needs a host tool, this
    # job must go red — that is the entire point.
    step "README parity guard"
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
    # doctor's job is to *report* environment gaps, so a non-zero exit on a
    # bare image is a legitimate diagnosis, not a crash. What must hold is:
    # it produces a report, and the only thing it calls FAIL is the missing
    # git runtime (bd#102). Any new hard failure turns this red.
    set +e
    bytedigger-engine doctor > /tmp/doctor.log 2>&1
    doctor_rc=$?
    set -e
    cat /tmp/doctor.log
    grep -qE '^doctor: [0-9]+ ok' /tmp/doctor.log || die "doctor produced no report (rc=$doctor_rc)"
    unexpected="$(grep -E '^\[FAIL\]' /tmp/doctor.log | grep -v 'git-runtime' || true)"
    [ -z "$unexpected" ] || die "doctor reported unexpected failures on a bare image:\n$unexpected"
    echo "ok: doctor reports; only expected bare-image failure is git-runtime"

    step "test suite COLLECTS on a bare image (the bd#97 gate)"
    # Execution needs host tools a bare image lacks (bd#102); collection does
    # not, and collection is exactly what bd#97 broke. 0 collection errors is
    # the honest blocking contract for a stock image.
    pip install -e ".[test]"
    if ! python3 -m pytest tests/ -q -p no:cacheprovider --collect-only >/tmp/collect.log 2>&1; then
      tail -40 /tmp/collect.log; die "suite does not collect on a clean tree"
    fi
    tail -1 /tmp/collect.log
    grep -E '^ERROR' /tmp/collect.log && die "collection reported errors" || true
    echo "ok: suite collects clean"
    ;;

  suite)
    install_bd102_tools
    step "install engine + test + security extras"
    setup_venv
    pip install -U pip
    # [security] carries semgrep, part of the closure set above.
    pip install -e ".[test,security]"
    step "bd#102 closure set present and documented"
    assert_closure_set_on_path

    step "README promise: doctor is clean once the dependencies are there"
    bytedigger-engine doctor

    step "full test suite"
    # `-rs` prints the reason for every skip. This lane's corpus arrives through
    # `git archive` — tracked files only, no `.git` — so tests whose subject is
    # the index skip here BY DESIGN. Without `-rs` that shows up only as the
    # skipped counter moving (68 -> 69), which is a number, not a fact: nobody
    # reading the log can tell which check did not run or why. Same idiom as
    # ci.yml:142. `-rfEs`, not `-rs`: `-r` REPLACES the default `fE`, so a bare
    # `-rs` would buy skip reasons at the cost of the FAILED/ERROR summary.
    python3 -m pytest tests/ -q -rfEs -p no:cacheprovider --timeout=180
    ;;

  expect-fail:bd102)
    # Self-closing gate for bd#102. If the suite ever passes on a bare image,
    # the closure set is dead weight and CI says so, loudly, that same day.
    step "expect-fail: full suite on a BARE image (bd#102 must still be open)"
    setup_venv
    pip install -U pip && pip install -e ".[test]"
    set +e
    python3 -m pytest tests/ -q -p no:cacheprovider --timeout=180 >/tmp/bare.log 2>&1
    rc=$?
    set -e
    tail -2 /tmp/bare.log
    if [ "$rc" = 0 ]; then
      die "the suite now PASSES on a bare image — bd#102 is closable.
      Delete scripts/clean-room/bd102-tools.manifest's entries, the
      install_bd102_tools block in this script, and the debt-watch job."
    fi
    echo "ok: still failing on a bare image (rc=$rc) — bd#102 remains open, closure set justified"
    ;;

  expect-fail:py39)
    # The issue asks for a matrix against the oldest supported Python.
    # pyproject declares >=3.9 and 3.9 does not work (see docs/clean-room.md).
    # Running it expect-fail satisfies the ask without shipping a permanently
    # red required check, and turns "fix 3.9" into a gate.
    step "expect-fail: collection on the declared minimum Python"
    setup_venv
    pip install -U pip && pip install -e ".[test]"
    set +e
    python3 -m pytest tests/ -q -p no:cacheprovider --collect-only >/tmp/c39.log 2>&1
    rc=$?
    set -e
    tail -2 /tmp/c39.log
    if [ "$rc" = 0 ]; then
      die "the suite now COLLECTS on $(python3 -V 2>&1) — the declared minimum
      Python works. Promote it to a real matrix leg and delete this mode."
    fi
    echo "ok: still broken on $(python3 -V 2>&1) (rc=$rc) — requires-python >=3.9 is still aspirational"
    ;;

  *)
    die "unknown mode: $MODE" ;;
esac

step "clean-room $MODE: PASS"
