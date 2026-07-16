"""RED tests for 585E30E3 P1a — engine-deterministic AST suite-safety gate.

Contract:
- New helper `scan_suite_safety(source: str) -> list[str]` in
  `suite_safety.py` detects module-level sys.path.insert and
  any-scope `from conftest import` using stdlib ast only.
- `_verify_red_lint_rules` in `phase_5_implement.py` calls `scan_suite_safety`
  BEFORE the semgrep-absent early-return at :1452, blocking with
  `E_RED_SUITE_UNSAFE` on any violation.

All tests MUST FAIL until GREEN implements `suite_safety.py` and the
call-site edit in `phase_5_implement.py`. RED-only file — do NOT implement.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_5_implement import _verify_red_lint_rules  # noqa: E402
from suite_safety import scan_suite_safety  # noqa: E402


# ─── shared helpers ───────────────────────────────────────────────────────────

def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": "scratchpad/spec.md",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _write_test_file(tmp_path: Path, relpath: str, content: str) -> str:
    """Write a file under tmp_path and return its relative path string."""
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return relpath


# ─── AC1-7: scan_suite_safety unit tests ──────────────────────────────────────


def test_ac01_empty_source_returns_empty_list():
    """AC1: scan_suite_safety('') → []."""
    result = scan_suite_safety("")
    assert result == [], f"Expected [], got {result!r}"


def test_ac02_module_level_sys_path_insert_flagged():
    """AC2: Module-level sys.path.insert(0, x) → violation list len 1, contains lineno + 'sys.path.insert'."""
    source = """\
import sys
sys.path.insert(0, "/some/path")
"""
    result = scan_suite_safety(source)
    assert len(result) == 1, f"Expected 1 violation, got {result!r}"
    assert "sys.path.insert" in result[0], f"Expected 'sys.path.insert' in violation: {result[0]!r}"
    # line 2 has the call
    assert "2" in result[0], f"Expected lineno '2' in violation: {result[0]!r}"


def test_ac03_scoping_precision_sys_path_inside_function_not_flagged():
    """AC3: sys.path.insert inside a function body → NOT flagged ([] if no other issue)."""
    source = """\
import sys

def setup():
    sys.path.insert(0, "/some/path")
"""
    result = scan_suite_safety(source)
    assert result == [], f"Expected [] for function-scoped sys.path.insert, got {result!r}"


def test_ac04_from_conftest_import_at_module_level_flagged():
    """AC4: from conftest import make_x at module level → violation len 1, lineno + 'conftest'."""
    source = """\
import pytest
from conftest import make_x
"""
    result = scan_suite_safety(source)
    assert len(result) == 1, f"Expected 1 violation, got {result!r}"
    assert "conftest" in result[0], f"Expected 'conftest' in violation: {result[0]!r}"
    assert "2" in result[0], f"Expected lineno '2' in violation: {result[0]!r}"


def test_ac05_from_conftest_inside_test_function_still_flagged():
    """AC5: from conftest import a inside a test function → STILL flagged (any-scope rule)."""
    source = """\
import pytest

def test_something():
    from conftest import a
    assert a is not None
"""
    result = scan_suite_safety(source)
    assert len(result) == 1, f"Expected 1 violation (any-scope), got {result!r}"
    assert "conftest" in result[0], f"Expected 'conftest' in violation: {result[0]!r}"
    assert "4" in result[0], f"Expected lineno '4' in violation: {result[0]!r}"


def test_ac06_clean_b1_style_file_returns_empty():
    """AC6: Clean B1-style file (pytest fixtures, no sys.path.insert, no conftest import) → []."""
    source = """\
from __future__ import annotations
import pytest

@pytest.fixture
def my_fixture(tmp_path):
    return tmp_path / "work"

def test_something(my_fixture):
    assert my_fixture.parent.exists()
"""
    result = scan_suite_safety(source)
    assert result == [], f"Expected [] for clean B1-style file, got {result!r}"


def test_ac07_forcing_function_exact_two_violations_with_correct_linenos():
    """AC7: Source with module-level sys.path.insert at line 5 AND from conftest import at line 9
    → returns EXACTLY 2 violations, one naming line 5, one naming line 9 (defeats return [] stub)."""
    # Carefully construct source so sys.path.insert is on line 5, conftest import on line 9
    source = (
        "import sys\n"           # line 1
        "import os\n"            # line 2
        "from pathlib import Path\n"  # line 3
        "\n"                     # line 4
        "sys.path.insert(0, str(Path(__file__).parent))\n"  # line 5
        "\n"                     # line 6
        "import pytest\n"        # line 7
        "\n"                     # line 8
        "from conftest import a, b\n"  # line 9
    )
    result = scan_suite_safety(source)
    assert len(result) == 2, f"Expected exactly 2 violations, got {result!r}"

    lines_mentioned = []
    for v in result:
        # each violation string starts with "<lineno>: ..."
        lineno_str = v.split(":")[0].strip()
        lines_mentioned.append(lineno_str)

    assert "5" in lines_mentioned, f"Expected a violation referencing line 5, got {result!r}"
    assert "9" in lines_mentioned, f"Expected a violation referencing line 9, got {result!r}"

    has_path_insert = any("sys.path.insert" in v for v in result)
    has_conftest = any("conftest" in v for v in result)
    assert has_path_insert, f"Expected a sys.path.insert violation, got {result!r}"
    assert has_conftest, f"Expected a conftest violation, got {result!r}"


# ─── AC8-12: _verify_red_lint_rules integration tests ────────────────────────


def test_ac08_verify_red_lint_rules_suite_unsafe_file_returns_error(tmp_path):
    """AC8: _verify_red_lint_rules with a red_test_path whose file has a banned pattern
    → status=='error', error_code=='E_RED_SUITE_UNSAFE', recoverable is True."""
    unsafe_source = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
        "\n"
        "def test_something(): assert True\n"
    )
    relpath = _write_test_file(tmp_path, "tests/test_unsafe_red.py", unsafe_source)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])
    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"Expected status='error', got {result.status!r}"
    assert result.error_code == "E_RED_SUITE_UNSAFE", (
        f"Expected error_code='E_RED_SUITE_UNSAFE', got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"Expected recoverable=True, got {result.recoverable!r}"
    )


def test_ac09_suite_unsafe_blocks_even_when_semgrep_absent(tmp_path, monkeypatch):
    """AC9: semgrep absent (shutil.which → None) + suite-unsafe red file
    → STILL status=='error' E_RED_SUITE_UNSAFE (NOT ok/skipped).
    Pins that suite-safety check runs BEFORE the :1452 semgrep early-return."""
    import phase_5_implement as p5

    monkeypatch.setattr(p5.shutil, "which", lambda _: None)

    unsafe_source = (
        "import sys\n"
        "sys.path.insert(0, '/fake/path')\n"
        "\n"
        "def test_foo(): assert False\n"
    )
    relpath = _write_test_file(tmp_path, "tests/test_unsafe_no_semgrep.py", unsafe_source)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])
    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' even when semgrep absent, got {result.status!r}"
    )
    assert result.error_code == "E_RED_SUITE_UNSAFE", (
        f"Expected E_RED_SUITE_UNSAFE even when semgrep absent, got {result.error_code!r}"
    )


def test_ac10_semgrep_absent_clean_files_fails_loud(tmp_path, monkeypatch):
    """AC10: semgrep absent + all red files clean → status=='error', E_RED_LINT_SEMGREP_MISSING (112CB15B fail-loud; semgrep is build-critical)."""
    import phase_5_implement as p5

    monkeypatch.setattr(p5.shutil, "which", lambda _: None)

    clean_source = (
        "import pytest\n"
        "\n"
        "def test_something(tmp_path):\n"
        "    assert (tmp_path / 'x').parent.exists()\n"
    )
    relpath = _write_test_file(tmp_path, "tests/test_clean_no_semgrep.py", clean_source)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])
    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' when semgrep absent (fail-loud), got {result.status!r}"
    )
    assert result.error_code == "E_RED_LINT_SEMGREP_MISSING", (
        f"Expected E_RED_LINT_SEMGREP_MISSING, got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"Expected recoverable=False, got {result.recoverable!r}"
    )


def test_ac11_error_string_contains_filepath_lineno_and_remediation(tmp_path):
    """AC11: Error string contains offending file path + lineno + the remediation phrase."""
    unsafe_source = (
        "from conftest import my_fixture\n"
        "\n"
        "def test_something(): assert True\n"
    )
    relpath = _write_test_file(tmp_path, "tests/test_conftest_import.py", unsafe_source)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev([relpath])
    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "error", f"Expected error, got {result.status!r}"
    assert result.error_code == "E_RED_SUITE_UNSAFE"

    error_str = result.error or ""
    # Must contain the file path (or basename)
    assert "test_conftest_import.py" in error_str, (
        f"Expected file path in error string, got: {error_str!r}"
    )
    # Must contain a lineno reference (line 1 for this source)
    assert "1" in error_str, f"Expected lineno in error string, got: {error_str!r}"
    # Must contain the remediation phrase about pytest injection or sys.path.insert removal
    remediation_present = (
        "pytest injection" in error_str
        or "sys.path.insert" in error_str
        or "conftest" in error_str
    )
    assert remediation_present, (
        f"Expected remediation phrase in error string, got: {error_str!r}"
    )


def test_ac12_scan_suite_safety_imported_from_suite_safety():
    """AC12: scan_suite_safety is imported into phase_5_implement.py from suite_safety
    (helper body NOT inlined in the entry-point file).

    Verified by: the import at the top of this test file (`from suite_safety import
    scan_suite_safety`) succeeds — if the module doesn't exist, the whole test file fails to
    import. Additionally, we confirm that phase_5_implement exposes the name via its own import
    (not just that the suite_safety module exists independently).
    """
    import importlib
    import suite_safety as safety_module

    # The function must exist in the dedicated suite_safety module
    assert hasattr(safety_module, "scan_suite_safety"), (
        "scan_suite_safety not found in suite_safety"
    )
    assert callable(safety_module.scan_suite_safety), (
        "scan_suite_safety in suite_safety is not callable"
    )

    # Confirm phase_5_implement imports from suite_safety (not inline)
    # by checking that phase_5_implement uses the same object reference
    import phase_5_implement as p5
    p5_fn = getattr(p5, "scan_suite_safety", None)
    assert p5_fn is not None, (
        "scan_suite_safety not found as a name in phase_5_implement; "
        "GREEN must `from suite_safety import scan_suite_safety` there"
    )
    assert p5_fn is safety_module.scan_suite_safety, (
        "phase_5_implement.scan_suite_safety is not the same object as "
        "suite_safety.scan_suite_safety — must be imported, not inlined"
    )
