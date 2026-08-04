"""RED tests for GH1111 / 80D89E6D: core-boundary-lint blind spots (AC1..AC12).

Spec: 2026-07-22_80D89E6D_core_boundary_lint_blind_spots_spec.md

§1q-ext compliance (D1CF5FDF): every `core_boundary` import is DEFERRED into the
test body, so this file COLLECTS cleanly and fails at assert time only.
conftest.py already puts the engine_py root on sys.path — no sys.path mutation here.

§1l (stub-passability): every test drives the real `core_boundary.scan_module` /
`lint_manifest`, or the real `core-boundary-lint.py` CLI via subprocess, over
tmp_path synthetic fixtures. Neither the library nor the CLI is ever mocked.

§1i: no singleton-resource or timing dependency — all fixtures are per-test
tmp_path trees; nothing contended is shared between tests.

AC → expected state TODAY (pre-GREEN):
  AC1  FAIL — module docstring with /Users/ + SHARED/ yields 2 violations today
               (D1 docstring exemption does not exist).
  AC2  FAIL — function docstring with SHARED/ yields a string_ref today.
  AC3  FAIL — class docstring with ~/.claude yields a path_literal today.
  AC4  PASS — anti-leak guard: non-docstring constant must stay flagged (already is).
  AC5  PASS — anti-leak guard: bare non-first Expr string must stay flagged (already is).
  AC6  FAIL — docstring carrying SHARED/ adds a 3rd violation today; spec wants
               exactly host_import + env_read.
  AC7  PASS — regression (ba0b4de4-1), already closed by _segment_anchored_patterns.
  AC8  PASS — regression (ba0b4de4-2), already closed by validate_manifest_schema.
  AC9  FAIL — `--list-closure` flag does not exist (argparse: unrecognized arguments).
  AC10 FAIL — `--list-closure --json` does not exist.
  AC11 FAIL — exits 2 today only via argparse's unrecognized-argument path, with no
               `ERROR ` manifest diagnostic on stderr; the AC asserts the real
               fail-CLOSED diagnostic, so it is red until D2 lands.
  AC12 FAIL — same as AC11, for the schema-failure path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ─── Absolute anchors (no hardcoded host paths; resolved from __file__) ───────

_ENGINE_PY_ROOT = Path(__file__).resolve().parent.parent   # engine_py/
_BUILD_DIR = _ENGINE_PY_ROOT.parent                        # bytedigger repo root
_LINT_CLI = _BUILD_DIR / "core-boundary-lint.py"


# ─── Helpers ─────────────────────────────────────────────────────────────────

_DEFAULT_FORBIDDEN = {
    "path_literals": ["/Users/", "~/.claude", ".claude/"],
    "string_refs": ["SHARED/", "MEMORY.md"],
    "env_prefixes": ["HAL_"],
    "import_denylist": ["continuity", "coord", "observability", "models_config"],
}


def _write_module(tmp_path: Path, name: str, source: str) -> Path:
    """Write a synthetic Python module verbatim (no dedent — docstring column
    position matters for these fixtures) and return its path."""
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    p = tmp_path / "core_manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT_CLI), *args],
        capture_output=True,
        text=True,
    )


# ─── AC1: module docstring is exempt ─────────────────────────────────────────

def test_ac1_module_docstring_is_exempt(tmp_path):
    """AC1: module docstring containing /Users/ and SHARED/ → zero violations.
    Fails today: scan_module reports path_literal + string_ref at line 1 (P3).
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "mod_docstring.py",
        '"""Docstring mentioning SHARED/ and /Users/ tokens."""\n\nVALUE = 42\n',
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    assert violations == [], (
        f"Expected module docstring to be exempt, got: {violations!r}"
    )


# ─── AC2: function docstring is exempt ───────────────────────────────────────

def test_ac2_function_docstring_is_exempt(tmp_path):
    """AC2: function docstring containing SHARED/ → zero violations.
    Fails today: string_ref reported on the docstring line.
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "func_docstring.py",
        "def helper():\n"
        '    """Reads state from SHARED/ by way of the host adapter."""\n'
        "    return 1\n",
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    assert violations == [], (
        f"Expected function docstring to be exempt, got: {violations!r}"
    )


# ─── AC3: class docstring is exempt ──────────────────────────────────────────

def test_ac3_class_docstring_is_exempt(tmp_path):
    """AC3: class docstring containing ~/.claude → zero violations.
    Fails today: path_literal reported on the docstring line.
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "class_docstring.py",
        "class Adapter:\n"
        '    """Host adapter; the host resolves things under ~/.claude at runtime."""\n'
        "    version = 1\n",
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    assert violations == [], (
        f"Expected class docstring to be exempt, got: {violations!r}"
    )


# ─── AC4: exemption does not leak to ordinary constants ──────────────────────

def test_ac4_non_docstring_constant_still_flagged(tmp_path):
    """AC4: module-level `X = "/Users/guy"` still yields path_literal.
    Anti-leak guard — passes today and MUST keep passing after D1.
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "assign_constant.py",
        '"""Perfectly innocent docstring."""\n\nX = "/Users/guy"\n',
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    kinds = [v.kind for v in violations]
    assert "path_literal" in kinds, (
        f"Expected path_literal for the non-docstring assignment, got: {violations!r}"
    )
    assert all(v.line == 3 for v in violations), (
        f"Expected every violation on line 3 (the assignment), got: {violations!r}"
    )


# ─── AC5: a bare non-first Expr string is not a docstring ────────────────────

def test_ac5_bare_expr_string_after_first_statement_still_flagged(tmp_path):
    """AC5: a bare string Expr that is the SECOND statement of a module is not a
    docstring and must still yield its string_ref violation.
    Anti-leak guard — passes today and MUST keep passing after D1.
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "bare_expr.py",
        "X = 1\n"
        '"SHARED/state/ofi-log.jsonl"\n',
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    kinds = [v.kind for v in violations]
    assert "string_ref" in kinds, (
        f"Expected string_ref for the second-statement bare string, got: {violations!r}"
    )
    assert any(v.line == 2 for v in violations), (
        f"Expected a violation on line 2, got: {violations!r}"
    )


# ─── AC6: host_import / env_read unaffected by D1 ────────────────────────────

def test_ac6_host_import_and_env_read_unaffected(tmp_path):
    """AC6: module with a (token-bearing) docstring PLUS `import coord` and
    `os.getenv("HAL_X")` yields exactly those 2 violations.
    Fails today: the docstring adds a 3rd (string_ref SHARED/) violation.
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "host_coupled.py",
        '"""Module docstring that talks about SHARED/ for documentation only."""\n'
        "import os\n"
        "import coord\n"
        "\n"
        "def read():\n"
        '    return coord, os.getenv("HAL_X")\n',
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    kinds = sorted(v.kind for v in violations)
    assert kinds == ["env_read", "host_import"], (
        f"Expected exactly ['env_read', 'host_import'], got {kinds!r} "
        f"from {violations!r}"
    )


# ─── AC7: regression — bare ".claude" still flagged (ba0b4de4-1) ─────────────

def test_ac7_regression_bare_dot_claude_flagged(tmp_path):
    """AC7: `Path.home() / ".claude"` (no trailing slash) is a path_literal.
    Passes today (closed by _segment_anchored_patterns); regression lock only.
    """
    from bytedigger_engine.core_boundary import scan_module  # noqa: PLC0415

    src = _write_module(
        tmp_path,
        "bare_claude.py",
        "from pathlib import Path\n"
        "\n"
        "def home_dir():\n"
        '    return Path.home() / ".claude"\n',
    )

    violations = scan_module(str(src), _DEFAULT_FORBIDDEN)

    path_literals = [v for v in violations if v.kind == "path_literal"]
    assert len(path_literals) == 1, (
        f"Expected exactly 1 path_literal for the bare '.claude' constant, "
        f"got: {violations!r}"
    )
    assert path_literals[0].line == 4, (
        f"Expected the violation on line 4, got: {path_literals[0]!r}"
    )


# ─── AC8: regression — missing `forbidden` key fails CLOSED (ba0b4de4-2) ─────

def test_ac8_regression_missing_forbidden_key_fails_closed(tmp_path):
    """AC8: manifest with core_modules but no `forbidden` key → ok=False,
    a 'forbidden missing or empty' error, and ZERO violations (no vacuous green).
    Passes today (closed by validate_manifest_schema); regression lock only.
    """
    from bytedigger_engine.core_boundary import lint_manifest  # noqa: PLC0415

    _write_module(tmp_path, "some_module.py", 'X = "/Users/guy"\n')
    manifest_path = _write_manifest(tmp_path, {"core_modules": ["some_module.py"]})

    result = lint_manifest(str(manifest_path), str(tmp_path))

    assert result.ok is False, f"Expected ok=False, got {result!r}"
    assert result.violations == [], (
        f"Expected zero violations on a schema failure, got: {result.violations!r}"
    )
    assert any("forbidden missing or empty" in e for e in result.errors), (
        f"Expected a 'forbidden missing or empty' error, got: {result.errors!r}"
    )


# ─── CLI closure-enumeration fixture ─────────────────────────────────────────

def _write_closure_fixture(tmp_path: Path) -> Path:
    """core_modules=[a.py]; a.py imports b (closure) and c (declared host).

    Returns the manifest path; the module root is tmp_path itself.
    """
    _write_module(
        tmp_path,
        "a.py",
        "import b\n"
        "import c\n"
        "\n"
        "def go():\n"
        "    return b, c\n",
    )
    _write_module(tmp_path, "b.py", "def helper():\n    return 1\n")
    _write_module(tmp_path, "c.py", "def host_helper():\n    return 2\n")
    return _write_manifest(tmp_path, {
        "core_modules": ["a.py"],
        "host_modules": ["c.py"],
        "forbidden": _DEFAULT_FORBIDDEN,
    })


# ─── AC9: --list-closure text mode ───────────────────────────────────────────

def test_ac9_list_closure_text_mode(tmp_path):
    """AC9: `--list-closure` prints one module path per line, includes every
    core_modules entry, excludes host_modules, exit 0.
    Fails today: the flag does not exist (argparse → exit 2, empty stdout).
    """
    manifest_path = _write_closure_fixture(tmp_path)

    proc = _run_cli(
        "--manifest", str(manifest_path),
        "--root", str(tmp_path),
        "--list-closure",
    )

    assert proc.returncode == 0, (
        f"Expected exit 0, got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    modules = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert "a.py" in modules, f"core module 'a.py' missing from {modules!r}"
    assert "b.py" in modules, f"closure module 'b.py' missing from {modules!r}"
    assert "c.py" not in modules, f"host module 'c.py' must be excluded: {modules!r}"


# ─── AC10: --list-closure --json ─────────────────────────────────────────────

def test_ac10_list_closure_json_mode(tmp_path):
    """AC10: `--list-closure --json` emits {"modules": [...]}, same set as AC9.
    Fails today: the flag does not exist.
    """
    manifest_path = _write_closure_fixture(tmp_path)

    proc = _run_cli(
        "--manifest", str(manifest_path),
        "--root", str(tmp_path),
        "--list-closure",
        "--json",
    )

    assert proc.returncode == 0, (
        f"Expected exit 0, got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict) and "modules" in payload, (
        f"Expected a dict with a 'modules' key, got: {payload!r}"
    )
    assert sorted(payload["modules"]) == ["a.py", "b.py"], (
        f"Expected ['a.py', 'b.py'], got {payload['modules']!r}"
    )


# ─── AC11: --list-closure + missing manifest → exit 2, fail CLOSED ───────────

def test_ac11_list_closure_missing_manifest_exits_2(tmp_path):
    """AC11: `--list-closure` against a missing manifest exits 2, emits no module
    lines, and reports the manifest error on stderr (§3 fail-CLOSED mapping).
    Fails today: argparse rejects the unknown flag, so stderr carries
    'unrecognized arguments' instead of the ERROR diagnostic.
    """
    missing = tmp_path / "nope_manifest.json"

    proc = _run_cli(
        "--manifest", str(missing),
        "--root", str(tmp_path),
        "--list-closure",
    )

    assert proc.returncode == 2, (
        f"Expected exit 2, got {proc.returncode}; stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "", (
        f"Expected no module lines on stdout, got: {proc.stdout!r}"
    )
    assert "unrecognized arguments" not in proc.stderr, (
        "--list-closure is not a recognised flag yet (argparse rejected it): "
        f"{proc.stderr!r}"
    )
    assert "ERROR" in proc.stderr, (
        f"Expected an ERROR diagnostic on stderr, got: {proc.stderr!r}"
    )


# ─── AC12: --list-closure + schema failure → exit 2 ──────────────────────────

def test_ac12_list_closure_schema_failure_exits_2(tmp_path):
    """AC12: `--list-closure` against a schema-invalid manifest exits 2 with one
    ERROR line per schema error and no module lines.
    Fails today: argparse rejects the unknown flag before any manifest work.
    """
    manifest_path = _write_manifest(tmp_path, {"core_modules": ["a.py"]})
    _write_module(tmp_path, "a.py", "X = 1\n")

    proc = _run_cli(
        "--manifest", str(manifest_path),
        "--root", str(tmp_path),
        "--list-closure",
    )

    assert proc.returncode == 2, (
        f"Expected exit 2, got {proc.returncode}; stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "", (
        f"Expected no module lines on stdout, got: {proc.stdout!r}"
    )
    assert "unrecognized arguments" not in proc.stderr, (
        "--list-closure is not a recognised flag yet (argparse rejected it): "
        f"{proc.stderr!r}"
    )
    assert "forbidden missing or empty" in proc.stderr, (
        f"Expected the schema error on stderr, got: {proc.stderr!r}"
    )
