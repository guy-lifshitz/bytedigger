"""RED tests for bd#256: CI runs the security-scan .bats suites.

Reads the real .github/workflows/ci.yml (yaml.safe_load, inside the tests, never
at import time), finds the step whose `run` text contains `bats` and both
tests/security-scan.bats and tests/security-scan-scope.bats, and executes that
step's own command text with plain `bash -e -c` (not pipefail: a GitHub `run:`
without `shell:` gets `bash -e {0}`) and real bats, in a scratch copy of tests/
(without *.py), scripts/ and phases/ under tmp_path. No stub of bats, no
re-implementation of the step.

T1 the step exists; T2 an unchanged copy exits 0; T3 one inverted assertion
exits non-zero with `not ok` and the cell name; T4 both suites with no @test
exit non-zero; T4b one suite with no @test and the other intact exits
non-zero; T5 a suite whose only cell calls `skip` exits non-zero.

Needs bats: when it is not on PATH the tests skip, or fail when the env var
BD_REQUIRE_BATS=1 is set (as the CI job does).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
SUITES = ("tests/security-scan.bats", "tests/security-scan-scope.bats")
CELL = "T01_classifies_auth_as_high"


def _need_bats() -> None:
    if shutil.which("bats") is None:
        if os.environ.get("BD_REQUIRE_BATS") == "1":
            pytest.fail("bats not on PATH and BD_REQUIRE_BATS=1")
        pytest.skip("bats not on PATH")


def _step_run() -> str:
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    for job in (data.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str) and "bats" in run and all(s in run for s in SUITES):
                return run
    pytest.fail(
        "ci.yml has no step whose run text contains bats and both "
        "tests/security-scan.bats and tests/security-scan-scope.bats"
    )


def _scratch(tmp_path: Path) -> Path:
    root = tmp_path / "scratch"
    shutil.copytree(REPO / "tests", root / "tests", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    shutil.copytree(REPO / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "phases", root / "phases", ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _run(run_text: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        # plain `bash -e`: what a GitHub `run:` without `shell:` gets (bash -e {0}), no pipefail
        ["bash", "-e", "-c", run_text],
        cwd=str(cwd), capture_output=True, text=True, timeout=300,
    )


def _out(p: subprocess.CompletedProcess) -> str:
    return (p.stdout or "") + (p.stderr or "")


def test_t1_step_exists():
    _step_run()


def test_t2_unchanged_copy_passes(tmp_path):
    run_text = _step_run()
    _need_bats()
    root = _scratch(tmp_path)
    p = _run(run_text, root)
    assert p.returncode == 0, _out(p)


def test_t3_inverted_assertion_fails_and_names_cell(tmp_path):
    run_text = _step_run()
    _need_bats()
    root = _scratch(tmp_path)
    f = root / "tests" / "security-scan.bats"
    src = f.read_text(encoding="utf-8")
    needle = '[ "$status" -eq 0 ]'
    assert needle in src
    f.write_text(src.replace(needle, '[ "$status" -eq 97 ]', 1), encoding="utf-8")
    p = _run(run_text, root)
    out = _out(p)
    assert p.returncode != 0, out
    assert "not ok" in out, out
    assert CELL in out, out


def test_t4_suites_without_cells_fail(tmp_path):
    run_text = _step_run()
    _need_bats()
    root = _scratch(tmp_path)
    for s in SUITES:
        (root / s).write_text("#!/usr/bin/env bats\n# no cells\n", encoding="utf-8")
    p = _run(run_text, root)
    assert p.returncode != 0, _out(p)


def test_t4b_one_empty_suite_fails(tmp_path):
    run_text = _step_run()
    _need_bats()
    for i, empty in enumerate(SUITES):
        root = _scratch(tmp_path / f"case{i}")
        (root / empty).write_text("#!/usr/bin/env bats\n# no cells\n", encoding="utf-8")
        p = _run(run_text, root)
        assert p.returncode != 0, f"{empty} empty, sibling intact: " + _out(p)


def test_t5_skipping_cell_fails(tmp_path):
    run_text = _step_run()
    _need_bats()
    root = _scratch(tmp_path)
    for s in SUITES:
        (root / s).write_text(
            '#!/usr/bin/env bats\n@test "only_cell" {\n  skip "nope"\n}\n',
            encoding="utf-8",
        )
    p = _run(run_text, root)
    assert p.returncode != 0, _out(p)
