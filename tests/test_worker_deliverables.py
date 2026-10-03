"""RED tests for bd#127 -- workers write their own deliverables.

Spec: SHARED/memory/Decisions/2026-09-30_bd127_worker_deliverables_spec.md
Covers AC1-AC3 (agent frontmatter + contract text), AC4/AC5 (bash gate only;
the TS twin is covered in scripts/ts/__tests__/worker-deliverables.test.ts --
local-only, CI does not run bun test, so exact reason strings are pinned HERE),
AC6 (parse-error reporting in BOTH learning-store backends) and AC7 (CI wiring).

Rev 2 (Opus gate) additions: exact reason strings, crash-safety when learning
keys / subdirs are absent (AC5c / R2), slice-scoped AC3 checks, empty-category
and whitespace/heading parse-error edges, CI wiring test. Tests marked "GUARD"
pass on main and protect post-GREEN behavior.

Nothing is imported from the repo at module level; scripts are driven through
subprocess inside tmp_path fixtures. No sys.path manipulation.

Tests marked "GUARD" pass on current main and protect post-GREEN behavior.
All other tests are expected RED today.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "build-gate.sh"
STORE = REPO_ROOT / "scripts" / "learning-store.sh"
SCHEMA = REPO_ROOT / "tests" / "fixtures" / "learning-schema.sql"

# bd#89 P3c: the synthesizer agent and its learnings-raw.md deliverable are retired,
# so the agent-frontmatter (AC1/AC2) and phase-7 contract (AC3) tests are gone. The
# learning-store extract tests (AC6/C6/C7) stay: extract still parses a
# learnings-raw.md when one exists.


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _clean_env(home: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Gate fixtures (AC4 / AC5) -- bash build-gate.sh only
# ---------------------------------------------------------------------------

def _gate_fixture(tmp_path: Path, phase: str, extra_state: str, complexity="FEATURE",
                  subdirs=("research", "architecture", "reviews"), with_scratch_dir=True,
                  scratch_name="scratch"):
    # F2: subdirs is optional so tests can exercise an absent architecture/ or reviews/.
    # Rev3 C2: scratch_name lets a test put a space in the scratchpad dir name.
    scratch = tmp_path / scratch_name
    scratch.mkdir(parents=True, exist_ok=True)
    for d in subdirs:
        (scratch / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    scratch_line = f'scratchpad_dir: "{scratch}"\n' if with_scratch_dir else ""
    state = (
        'task: "t"\n'
        f"complexity: {complexity}\n"
        "mode: AUTONOMOUS\n"
        f'current_phase: "{phase}"\n'
        'last_updated: "2026-09-30T00:00:00Z"\n'
        f"{scratch_line}"
        f"{extra_state}"
    )
    (tmp_path / "build-state.yaml").write_text(state)
    return scratch


def _run_gate(tmp_path: Path, extra_path: Path | None = None):
    env = _clean_env(tmp_path, BYTEDIGGER_CONFIG=str(tmp_path / "bytedigger.json"))
    if extra_path is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(GATE)], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, env=env, cwd=str(tmp_path), timeout=60,
    )


def _reason(proc) -> str:
    return json.loads(proc.stdout.strip().splitlines()[-1])["reason"]


# ---------------------------------------------------------------------------
# AC4 -- gate 4
# ---------------------------------------------------------------------------

# bd#89 P2a: the AC4 gate-4 cases (scratchpad_stale on findings-*.md, approach-*.md
# deliverable) are retired -- phase 4 and its gate are dropped. The phase-5 gate
# behaviour is pinned in engine_py/tests/test_bd89_p2a_phases_1_4_dropped.py (AC10).

# Phase-5 gate state after P2a: plan_review replaces phase_4_architect.
_P5_STATE = "plan_review: pass\nphase_5_implement: complete\nopus_validation: pass\n"


# ---------------------------------------------------------------------------
# AC5 -- gate 7
# ---------------------------------------------------------------------------

# bd#89 P3c: the learnings-raw.md deliverable entry is retired from gate 7; the
# deliverable-presence cases (missing / zero-byte / header-only / reviews-dir-absent)
# are gone. Gate 7 now checks review_complete only; these retargeted cases pin that.

def test_ac5_missing_learnings_raw_does_not_block(tmp_path):
    _gate_fixture(tmp_path, "7", "review_complete: pass\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "learnings-raw" not in proc.stdout


def test_ac5_review_complete_missing_is_the_only_reason(tmp_path):
    _gate_fixture(tmp_path, "7", "")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == "review_complete=pass (got: <missing>); "


def test_ac5_no_scratchpad_dir_no_deliverable_entry(tmp_path):
    # phase 7 without scratchpad_dir must not build "/reviews/learnings-raw.md".
    _gate_fixture(tmp_path, "7", "review_complete: pass\n", with_scratch_dir=False)
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "learnings-raw" not in proc.stdout


def test_ac5c_backend_file_no_extracted_passes_without_raw(tmp_path):
    # learning_backend set, learnings_extracted absent, no raw file -> exit 0.
    _gate_fixture(tmp_path, "7", "review_complete: pass\nlearning_backend: file\n")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ac5_trivial_missing_file_not_checked(tmp_path):
    # TRIVIAL never gains a learnings-raw check.
    _gate_fixture(tmp_path, "7", "review_complete: pass\n", complexity="TRIVIAL")
    proc = _run_gate(tmp_path)
    assert "learnings-raw" not in proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Rev 3 -- C2 (spaces in scratchpad path), C3 (TRIVIAL), C4 (no git diff by architect)
# ---------------------------------------------------------------------------

def test_c2_gate4_scratchpad_path_with_space_passes(tmp_path):
    # bd#89 P2a: re-pointed from gate 4 (dropped) to a phase-5 fixture; a spaced
    # scratchpad path must not break the gate.
    _gate_fixture(tmp_path, "5", _P5_STATE, scratch_name="my scratch")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_c2_gate7_scratchpad_path_with_space_passes(tmp_path):
    # bd#89 P3c: no learnings-raw.md needed; a spaced scratchpad path must not break gate 7.
    _gate_fixture(tmp_path, "7", "review_complete: pass\n", scratch_name="my scratch")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_c2_gate7_spaced_path_review_complete_missing_soft_blocks(tmp_path):
    _gate_fixture(tmp_path, "7", "", scratch_name="my scratch")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _reason(proc) == "review_complete=pass (got: <missing>); "


def test_c3_gate7_trivial_without_review_complete_passes(tmp_path):
    # bash used to demand review_complete even for TRIVIAL (TS already skips).
    _gate_fixture(tmp_path, "7", "", complexity="TRIVIAL")
    proc = _run_gate(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "review_complete" not in proc.stdout


# test_c4_phase4_reanchor_architect_does_not_run_git_diff retired by bd#89 P2a
# (phases/phase-4-architect.md deleted).


def test_c11_gate_survives_gnu_stat(tmp_path):
    # C11: on GNU coreutils `stat -f` means --file-system and exits 0 with
    # multi-line output, so `stat -f %m ... || stat -c %Y ...` never falls back
    # and the arithmetic on $mtime dies. The gate must survive a GNU-style stat.
    # bd#89 P2a: re-pointed from gate 4 (dropped) to a phase-5 fixture.
    _gate_fixture(tmp_path, "5", _P5_STATE)

    shim = tmp_path / "shim"
    shim.mkdir()
    stat_shim = shim / "stat"
    stat_shim.write_text(
        "#!/bin/bash\n"
        'last="${@: -1}"\n'
        "has_f=0; has_c=0; has_y=0\n"
        'for a in "$@"; do\n'
        '  [ "$a" = "-f" ] && has_f=1\n'
        '  [ "$a" = "-c" ] && has_c=1\n'
        '  [ "$a" = "%Y" ] && has_y=1\n'
        "done\n"
        'if [ "$has_f" = 1 ]; then\n'
        '  printf \'  File: "%s"\\n    ID: 0 Namelen: 255 Type: apfs\\n\' "$last"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$has_c" = 1 ] && [ "$has_y" = 1 ]; then\n'
        "  python3 -c 'import os,sys;print(int(os.stat(sys.argv[1]).st_mtime))' \"$last\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    stat_shim.chmod(0o755)

    proc = _run_gate(tmp_path, extra_path=shim)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unbound variable" not in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# AC6 -- extract reports parse errors (both backends)
# ---------------------------------------------------------------------------

T12_BODY = (
    "## New Learnings\n"
    "\n"
    "- [architecture] --- Good entry that should be stored\n"
    "- This line has no category prefix and should be skipped\n"
    "- [bug-fix] --- Another valid entry\n"
    "- [MALFORMED without closing bracket --- should be skipped\n"
    "- just plain text, no structure\n"
)
VALID_BODY = (
    "## New Learnings\n"
    "\n"
    "- [architecture] --- Good entry that should be stored\n"
    "- [bug-fix] --- Another valid entry\n"
)
PROSE_BODY = (
    "## New Learnings\n"
    "\n"
    "1. [workflow] — x\n"
    "**workflow**: y\n"
)

BACKENDS = ["file", "sqlite"]


def _extract(tmp_path: Path, backend: str, body: str):
    if backend == "sqlite" and shutil.which("sqlite3") is None:
        # F8/R7: CI sets BD_REQUIRE_SQLITE=1 so the sqlite half can never silently skip.
        if os.environ.get("BD_REQUIRE_SQLITE") == "1":
            pytest.fail("sqlite3 binary not available but BD_REQUIRE_SQLITE=1")
        pytest.skip("sqlite3 binary not available")
    cfg = tmp_path / "bytedigger.json"
    cfg.write_text(json.dumps({"learning": {
        "backend": backend, "max_inject": 10, "max_stored": 200,
        "storage_path": ".bytedigger/learnings"}}))
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True)
    (scratch / "reviews" / "learnings-raw.md").write_text(body, encoding="utf-8")
    env = _clean_env(tmp_path, BYTEDIGGER_CONFIG=str(cfg))
    if backend == "sqlite":
        db = tmp_path / "learnings.db"
        subprocess.run(["sqlite3", str(db)], input=SCHEMA.read_text(),
                       text=True, check=True, env=env, timeout=60)
        env["LEARNING_DB_URL"] = str(db)
    proc = subprocess.run(
        ["bash", str(STORE), "extract", str(scratch), "--config", str(cfg)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60,
    )
    state = (tmp_path / "build-state.yaml").read_text()
    return proc, state


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_guard_t12_extracted_count_and_exit_unchanged(tmp_path, backend):
    # GUARD: passes on main (2 extracted, exit 0).
    proc, state = _extract(tmp_path, backend, T12_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_t12_reports_three_parse_errors(tmp_path, backend):
    proc, state = _extract(tmp_path, backend, T12_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 3$", state, re.M)
    # M6: exactly ONE WARN line, and that same line carries the count and the format.
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert len(warn) == 1, proc.stderr
    assert re.search(r"\b3\b", warn[0]), warn[0]
    assert "- [category] --- lesson" in warn[0], warn[0]


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_all_valid_reports_zero_and_no_warn(tmp_path, backend):
    proc, state = _extract(tmp_path, backend, VALID_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_numbered_list_and_prose_count_as_parse_errors(tmp_path, backend):
    proc, state = _extract(tmp_path, backend, PROSE_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 0$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 2$", state, re.M)
    assert "WARN" in proc.stderr


EMPTYCAT_BODY = VALID_BODY + "- [!!!] --- lesson with no usable category\n"
BLANKISH_BODY = VALID_BODY + "   \n\t\n  ## indented heading\n"
HEADER_ONLY_BODY = "## New Learnings\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_empty_sanitized_category_counts_as_parse_error(tmp_path, backend):
    # F4/R4: `- [!!!] --- lesson` sanitizes to an empty category -> not a learning -> +1 error.
    # sqlite drops it in the bash read loop, so a python-only counter would miss it.
    proc, state = _extract(tmp_path, backend, EMPTYCAT_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 1$", state, re.M)
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert len(warn) == 1, proc.stderr
    assert re.search(r"\b1\b", warn[0]) and "- [category] --- lesson" in warn[0]


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_whitespace_only_and_indented_heading_are_not_errors(tmp_path, backend):
    # M5/R4: blank = strip()=="" ; heading = strip().startswith("#") -- same on both backends.
    proc, state = _extract(tmp_path, backend, BLANKISH_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


INDENTED_BODY = "## New Learnings\n\n  - [bug-fix] --- indented lesson\n"
FENCED_T12_BODY = "```markdown\n" + T12_BODY + "```\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_c6_indented_valid_bullet_is_stored_in_both_backends(tmp_path, backend):
    # Rev3 C6: file backend matches line.strip() like sqlite (sqlite is already a guard).
    proc, state = _extract(tmp_path, backend, INDENTED_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 1$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


@pytest.mark.parametrize("backend", BACKENDS)
def test_c7_code_fence_lines_are_not_parse_errors(tmp_path, backend):
    # Rev3 C7: ```markdown / ``` fence lines are skipped; T12 content still yields 3 errors.
    proc, state = _extract(tmp_path, backend, FENCED_T12_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 2$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 3$", state, re.M)
    warn = [l for l in proc.stderr.splitlines() if "WARN" in l]
    assert len(warn) == 1, proc.stderr
    assert re.search(r"\b3\b", warn[0]), warn[0]


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac6_header_only_reports_zero_no_warn(tmp_path, backend):
    # M5/R4: header-only file (sqlite early-exit path included) still writes 0, no WARN.
    proc, state = _extract(tmp_path, backend, HEADER_ONLY_BODY)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^learnings_extracted: 0$", state, re.M)
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M)
    assert "WARN" not in proc.stderr


# ---------------------------------------------------------------------------
# AC7 -- CI wiring (F8 / R7)
# ---------------------------------------------------------------------------

def test_ac7_ci_manifests_job_runs_this_suite_with_sqlite_required():
    import yaml  # pyyaml is installed in the manifests job (pip install pytest pyyaml)

    ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["manifests"]
    steps = [s for s in job["steps"]
             if "tests/test_worker_deliverables.py" in str(s.get("run", ""))]
    assert steps, "manifests job has no step running tests/test_worker_deliverables.py"
    step = steps[0]
    assert "pytest" in step["run"]
    # YAML may parse an unquoted 1 as int; compare as string.
    assert str((step.get("env") or {}).get("BD_REQUIRE_SQLITE")) == "1", \
        "CI step must set BD_REQUIRE_SQLITE=1 so the sqlite half fails instead of skipping"
