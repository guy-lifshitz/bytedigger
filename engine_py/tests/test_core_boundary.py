"""RED tests for D58780A2: E0 core-boundary-lint.

Spec: 2026-06-18_D58780A2_core_boundary_lint_spec.md

§1q-ext compliance (D1CF5FDF): core_boundary.py does NOT exist yet.
All imports of `core_boundary` symbols are DEFERRED to inside each test
function body. This file COLLECTS cleanly; tests FAIL at call/assert time
(ImportError from the deferred import), never at collection time.

conftest.py (the import-time singleton, §1q / 81F97F3D) already adds the
engine_py root to sys.path — no sys.path manipulation here.

§1l (stub-passability): every test calls the real lint_manifest / scan_module
on tmp-path synthetic fixtures. The UUT is NEVER mocked or patched.

§1i: no singleton-resource or timing races (§1i N/A — no file locks / ports).

§1f (AC12 structural note): tests import from core_boundary (the lib), never
from core-boundary-lint.py (the CLI entry-point). This is enforced purely by
the import statements inside each test body.

Expected FAIL today (all, because core_boundary.py does not exist):
  AC1  — ImportError: no module named 'core_boundary'
  AC2  — ImportError
  AC3  — ImportError
  AC4  — ImportError
  AC5  — ImportError
  AC6  — ImportError
  AC7  — ImportError
  AC8a — ImportError (load_manifest raises test)
  AC8b — ImportError (lint_manifest bad-manifest test)
  AC9  — ImportError
  AC10 — ImportError (ALSO fails because core_manifest.json does not exist yet)
  AC11 — ImportError

All 12 tests expected to fail today. After GREEN: only those asserting genuinely
absent behaviour remain red; AC10 additionally requires core_manifest.json and
all 14 seeded modules to be present and clean.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

# ─── Absolute path anchors (§1l real-anchor for AC10) ─────────────────────────

_ENGINE_PY_ROOT = Path(__file__).parent.parent  # engine_py/
_REAL_MANIFEST = _ENGINE_PY_ROOT / "core_manifest.json"

# bd#44 layout: the manifest still lives at the engine_py root, but its paths
# are relative to the PACKAGE root (`engine_py/bytedigger_engine/`) — the tree
# that actually ships. `.github/workflows/ci.yml` ("manifest parity") resolves
# them the same way. AC10 must lint against that root, not the HAL-flat one.
_PKG_ROOT = _ENGINE_PY_ROOT / "bytedigger_engine"


# ─── Helpers (write synthetic module files into tmp_path) ─────────────────────

def _write_module(tmp_path: Path, name: str, source: str) -> Path:
    """Write a synthetic Python module to tmp_path and return its path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(source))
    return p


def _write_manifest(tmp_path: Path, modules: list[str], forbidden: dict | None = None) -> Path:
    """Write a synthetic manifest JSON; returns its path."""
    if forbidden is None:
        forbidden = {
            "path_literals": ["/Users/", "~/.claude", ".claude/"],
            "string_refs": ["SHARED/", "MEMORY.md"],
            "env_prefixes": ["HAL_"],
            "import_denylist": ["continuity", "coord", "observability", "models_config"],
        }
    manifest = {"core_modules": modules, "forbidden": forbidden}
    p = tmp_path / "core_manifest.json"
    p.write_text(json.dumps(manifest))
    return p


# ─── AC1: clean synthetic module → ok=True, no violations, no errors ──────────

def test_ac1_clean_module_ok(tmp_path):
    """AC1: manifest lists one clean synthetic module → Result.ok True,
    violations==[], errors==[].
    Fails today: ImportError (core_boundary does not exist).
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    _write_module(tmp_path, "clean.py", """\
        # A perfectly clean module — no HAL host references.
        def add(a, b):
            return a + b
        """)
    manifest_path = _write_manifest(tmp_path, ["clean.py"])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is True, (
        f"Expected ok=True for clean module, got ok={result.ok!r}; "
        f"violations={result.violations!r}; errors={result.errors!r}"
    )
    assert result.violations == [], (
        f"Expected no violations, got: {result.violations!r}"
    )
    assert result.errors == [], (
        f"Expected no errors, got: {result.errors!r}"
    )


# ─── AC2: path_literal violation ─────────────────────────────────────────────

def test_ac2_path_literal_violation(tmp_path):
    """AC2: module with '/Users/guy/.claude/x' literal → kind=path_literal,
    correct module + line.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "pathliteral.py"
    _write_module(tmp_path, src_name, """\
        def bad():
            path = "/Users/guy/.claude/x"
            return path
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        f"Expected ok=False for path_literal violation, got ok={result.ok!r}"
    )
    assert len(result.violations) >= 1, (
        f"Expected >=1 violation, got: {result.violations!r}"
    )
    v = result.violations[0]
    assert v.kind == "path_literal", (
        f"Expected kind='path_literal', got {v.kind!r}"
    )
    assert src_name in v.module or v.module.endswith(src_name), (
        f"Expected module to contain '{src_name}', got {v.module!r}"
    )
    assert isinstance(v.line, int) and v.line > 0, (
        f"Expected positive line number, got {v.line!r}"
    )


# ─── AC3: env_read violation ──────────────────────────────────────────────────

def test_ac3_env_read_violation(tmp_path):
    """AC3: module calling os.getenv('HAL_FOO') → kind=env_read,
    detail contains 'HAL_FOO'.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "envread.py"
    _write_module(tmp_path, src_name, """\
        import os

        def get_value():
            return os.getenv("HAL_FOO")
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        f"Expected ok=False for env_read violation"
    )
    env_violations = [v for v in result.violations if v.kind == "env_read"]
    assert len(env_violations) >= 1, (
        f"Expected >=1 env_read violation, got violations: {result.violations!r}"
    )
    ev = env_violations[0]
    assert "HAL_FOO" in ev.detail, (
        f"Expected 'HAL_FOO' in detail, got {ev.detail!r}"
    )


# ─── AC4: host_import violation ───────────────────────────────────────────────

def test_ac4_host_import_continuity(tmp_path):
    """AC4a: module with 'import continuity' → kind=host_import.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "hostimport_continuity.py"
    _write_module(tmp_path, src_name, """\
        import continuity

        def run():
            return continuity.list()
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False for host_import (continuity)"
    )
    host_violations = [v for v in result.violations if v.kind == "host_import"]
    assert len(host_violations) >= 1, (
        f"Expected >=1 host_import violation, got: {result.violations!r}"
    )


def test_ac4_host_import_from_coord(tmp_path):
    """AC4b: module with 'from coord import x' → kind=host_import.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "hostimport_coord.py"
    _write_module(tmp_path, src_name, """\
        from coord import claim

        def acquire(uid):
            return claim(uid)
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False for host_import (from coord import x)"
    )
    host_violations = [v for v in result.violations if v.kind == "host_import"]
    assert len(host_violations) >= 1, (
        f"Expected >=1 host_import violation via 'from coord import', "
        f"got: {result.violations!r}"
    )


# ─── AC5: string_ref violation ────────────────────────────────────────────────

def test_ac5_string_ref_violation(tmp_path):
    """AC5: module with 'SHARED/state/x.jsonl' string → kind=string_ref.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "stringref.py"
    _write_module(tmp_path, src_name, """\
        def get_log_path():
            return "SHARED/state/x.jsonl"
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False for string_ref violation"
    )
    ref_violations = [v for v in result.violations if v.kind == "string_ref"]
    assert len(ref_violations) >= 1, (
        f"Expected >=1 string_ref violation, got: {result.violations!r}"
    )


# ─── AC6: N>1 multi-kind violations (§1l forcing function) ───────────────────

def test_ac6_multi_kind_violations_both_env_and_path(tmp_path):
    """AC6 (§1l N>1): module with BOTH os.getenv('HAL_X') AND '/Users/' literal
    → len(violations) >= 2 covering both kinds.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "multiviolation.py"
    _write_module(tmp_path, src_name, """\
        import os

        def multi():
            root = "/Users/guy/.claude/data"
            key = os.getenv("HAL_X")
            return root, key
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False for multi-violation module"
    )
    assert len(result.violations) >= 2, (
        f"Expected >=2 violations covering env_read + path_literal, "
        f"got {len(result.violations)}: {result.violations!r}"
    )
    kinds_found = {v.kind for v in result.violations}
    assert "env_read" in kinds_found, (
        f"Expected env_read violation, kinds found: {kinds_found!r}"
    )
    assert "path_literal" in kinds_found, (
        f"Expected path_literal violation, kinds found: {kinds_found!r}"
    )


# ─── AC7: missing module file → errors non-empty, ok False ───────────────────

def test_ac7_missing_module_file_driver_error(tmp_path):
    """AC7 (fail-closed): manifest lists a module file that does not exist →
    Result.errors non-empty, ok=False.
    CLI contract: exit 2 (driver error). Tested here at the lib level.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    # Write manifest referencing a file that does NOT exist on disk.
    manifest_path = _write_manifest(tmp_path, ["nonexistent_module.py"])
    assert not (tmp_path / "nonexistent_module.py").exists(), (
        "Fixture error: the module file must NOT exist for AC7"
    )

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False when a manifested module file is missing"
    )
    assert len(result.errors) >= 1, (
        f"Expected >=1 error surfacing the missing module, got errors={result.errors!r}"
    )
    # At least one error message should mention the missing module name.
    combined_errors = " ".join(result.errors)
    assert "nonexistent_module" in combined_errors, (
        f"Expected 'nonexistent_module' mentioned in errors, got: {result.errors!r}"
    )


# ─── AC8: missing/malformed manifest ──────────────────────────────────────────

def test_ac8a_load_manifest_raises_on_missing_file(tmp_path):
    """AC8a: load_manifest raises ManifestError when manifest path is missing.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import load_manifest  # noqa: PLC0415

    missing_path = str(tmp_path / "does_not_exist.json")
    with pytest.raises(Exception) as exc_info:
        load_manifest(missing_path)

    # Accept ManifestError or any exception that is NOT just a plain FileNotFoundError
    # silently swallowed — the contract says ManifestError is raised.
    exc_type_name = type(exc_info.value).__name__
    assert exc_type_name in ("ManifestError", "FileNotFoundError", "OSError"), (
        f"Expected ManifestError (or FileNotFoundError) for missing manifest, "
        f"got {exc_type_name}: {exc_info.value}"
    )


def test_ac8b_load_manifest_raises_on_malformed_json(tmp_path):
    """AC8b: load_manifest raises ManifestError on malformed JSON.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import load_manifest  # noqa: PLC0415

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not: valid json!!!")

    with pytest.raises(Exception) as exc_info:
        load_manifest(str(bad_json))

    exc_type_name = type(exc_info.value).__name__
    assert exc_type_name in ("ManifestError", "JSONDecodeError", "ValueError"), (
        f"Expected ManifestError for malformed JSON, got {exc_type_name}: {exc_info.value}"
    )


def test_ac8c_lint_manifest_bad_manifest_returns_errors(tmp_path):
    """AC8c: lint_manifest(bad_manifest_path) → errors non-empty, ok=False.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    missing_manifest = str(tmp_path / "no_such_manifest.json")

    result = lint_manifest(missing_manifest, str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False when manifest file is missing"
    )
    assert len(result.errors) >= 1, (
        f"Expected >=1 error for missing manifest, got errors={result.errors!r}"
    )


# ─── AC9: SyntaxError in module → errors non-empty, ok False ─────────────────

def test_ac9_module_with_syntax_error_is_driver_error(tmp_path):
    """AC9 (fail-closed): a manifested module with a Python SyntaxError →
    Result.errors non-empty, ok=False. NOT silently skipped-as-clean.
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "syntax_bad.py"
    bad_py = tmp_path / src_name
    bad_py.write_text("def broken(\n    # unclosed paren and nothing else\n")
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, (
        "Expected ok=False for module with SyntaxError — must NOT be silently clean"
    )
    assert len(result.errors) >= 1, (
        f"Expected >=1 error naming the broken module, got errors={result.errors!r}"
    )
    combined_errors = " ".join(result.errors)
    assert src_name in combined_errors or "syntax" in combined_errors.lower(), (
        f"Expected error mentioning '{src_name}' or 'syntax', got: {result.errors!r}"
    )


# ─── AC10: real manifest over real engine_py root (§1l real-anchor) ───────────

def test_ac10_real_manifest_all_seeded_modules_clean():
    """AC10 (§1l real-anchor): the committed core_manifest.json over the real
    engine_py/ root → Result.ok True, all 14 seeded modules clean.

    Fails today for TWO reasons:
      1. core_boundary.py does not exist → ImportError.
      2. core_manifest.json does not exist → lint_manifest would return errors.
    After GREEN: both files exist; all 14 seeded modules must genuinely produce
    zero violations.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    assert _REAL_MANIFEST.exists(), (
        f"core_manifest.json does not exist yet at {_REAL_MANIFEST} — "
        "this will pass only after GREEN creates it"
    )

    result = lint_manifest(str(_REAL_MANIFEST), str(_PKG_ROOT))

    assert result.errors == [], (
        f"Every manifested module must resolve under the package root "
        f"{_PKG_ROOT} — an unresolvable path is a manifest defect, not a clean "
        f"scan. errors={result.errors!r}"
    )
    assert result.ok is True, (
        f"Expected ok=True for real manifest over real package root. "
        f"violations={result.violations!r}; errors={result.errors!r}"
    )
    assert result.violations == [], (
        f"Real seeded modules must have zero violations. "
        f"Found: {result.violations!r}"
    )
    assert result.errors == [], (
        f"Real seeded modules must produce zero errors. "
        f"Found: {result.errors!r}"
    )


# ─── AC11: comment-only occurrence → AST immune, ok True ─────────────────────

def test_ac11_path_in_comment_is_immune(tmp_path):
    """AC11 (AST-precision): a module whose only '/Users/' occurrence is in a
    # comment line → Result.ok True (AST does not see comments).
    Fails today: ImportError.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    src_name = "comment_only.py"
    _write_module(tmp_path, src_name, """\
        # This module references /Users/guy/.claude in a comment — immune to AST lint.

        def clean_function():
            return 42
        """)
    manifest_path = _write_manifest(tmp_path, [src_name])

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is True, (
        f"Expected ok=True when '/Users/' only appears in a comment, "
        f"got ok={result.ok!r}; violations={result.violations!r}"
    )
    assert result.violations == [], (
        f"Expected no violations for comment-only reference, got: {result.violations!r}"
    )


# ─── AC12 structural note (§1f) ───────────────────────────────────────────────
# Every test above imports `from bytedigger_engine.core_boundary import <symbol>` — the lib, NOT
# the CLI entry-point (`core-boundary-lint.py`). This satisfies §1f structurally.
# No runtime assertion is needed; the import site IS the assertion.
