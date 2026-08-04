"""Exit-code ladders of the core-boundary-lint CLI driver (bd#48 follow-up).

The CLI was ported to the bytedigger repo root in bd#48. Its `--list-closure`
branch arrived under test (the four ACs ported from GH1111 kill 3/3 mutants
there), but three OTHER ladders shipped with zero assertions on them — measured,
not assumed: mutating `return N -> return 9` at each site and running the full
gh1111 + test_core_boundary set killed 0/3 (default mode), 0/4 (--packaging)
and 0/1 (argparse failure).

That is the "guard is green and inert" class: the boundary gate is the thing
that keeps host coupling out of the OSS package, and the code it exits with is
how CI learns the verdict. A driver that silently returns 0 on violations would
pass every CI run while enforcing nothing.

UUT = `core-boundary-lint.py` (the CLI entry-point ONLY). The library
`bytedigger_engine.core_boundary` is covered by test_core_boundary.py; nothing
here re-tests it. Every AC drives the REAL script through subprocess against
REAL files on disk and asserts the REAL process exit code — the driver is never
imported, mocked or patched.

Ladders under test (§2.3 fail-CLOSED semantics — errors beat violations beat clean):
  default mode  : 0 clean · 1 violations · 2 errors
  --packaging   : 0 clean · 1 violations · 2 driver error
  argparse      : 2 on a bad command line (never 0, never a traceback)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve()
_ENGINE_PY_ROOT = _THIS.parents[1]                 # engine_py/
_REPO_ROOT = _ENGINE_PY_ROOT.parent                # bytedigger repo root
_LINT_CLI = _REPO_ROOT / "core-boundary-lint.py"

_FORBIDDEN = {
    "path_literals": ["/Users/", "~/.claude", ".claude/"],
    "string_refs": ["SHARED/", "MEMORY.md"],
    "env_prefixes": ["HAL_"],
    "import_denylist": ["continuity", "coord", "observability", "models_config"],
}

_CLEAN_MODULE = '"""A module with nothing forbidden in it."""\n\n\ndef f():\n    return 1\n'
_DIRTY_MODULE = '"""Reads the ambient environment."""\nimport os\n\n\ndef f():\n    return os.environ.get("HAL_ANYTHING")\n'


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT_CLI), *args],
        capture_output=True,
        text=True,
    )


def _tree(tmp_path: Path, module_source: str, *, module: str = "mod.py") -> tuple[Path, Path]:
    """Write one manifested module + its manifest. Returns (manifest, root)."""
    root = tmp_path / "root"
    root.mkdir()
    (root / module).write_text(module_source, encoding="utf-8")
    manifest = tmp_path / "core_manifest.json"
    manifest.write_text(
        json.dumps({"core_modules": [module], "host_modules": [], "forbidden": _FORBIDDEN}),
        encoding="utf-8",
    )
    return manifest, root


def test_ac1_default_mode_clean_tree_exits_0(tmp_path):
    """Default ladder, rung 0: nothing forbidden anywhere → exit 0, no violations printed."""
    manifest, root = _tree(tmp_path, _CLEAN_MODULE)

    r = _run_cli("--manifest", str(manifest), "--root", str(root))

    assert r.returncode == 0, f"expected 0 on a clean tree; stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "BOUNDARY-VIOLATION" not in r.stdout, f"clean tree must print no violations; stdout={r.stdout!r}"


def test_ac2_default_mode_violation_exits_1_and_names_it(tmp_path):
    """Default ladder, rung 1: a manifested module reads the ambient env → exit 1.

    Guards the mutation `return 1 -> return 0`, which would let every boundary
    violation ship while CI stayed green.
    """
    manifest, root = _tree(tmp_path, _DIRTY_MODULE)

    r = _run_cli("--manifest", str(manifest), "--root", str(root))

    assert r.returncode == 1, (
        f"a real env_read violation must exit 1, not {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "BOUNDARY-VIOLATION" in r.stdout, f"the violation must be named on stdout; stdout={r.stdout!r}"
    assert "env_read" in r.stdout, f"the violation kind must be named; stdout={r.stdout!r}"


def test_ac3_default_mode_unresolvable_module_exits_2(tmp_path):
    """Default ladder, rung 2: a manifested path that does not exist is an ERROR,
    not a clean scan. Fail-closed — errors outrank violations."""
    manifest, root = _tree(tmp_path, _CLEAN_MODULE)
    manifest.write_text(
        json.dumps({
            "core_modules": ["mod.py", "does_not_exist.py"],
            "host_modules": [],
            "forbidden": _FORBIDDEN,
        }),
        encoding="utf-8",
    )

    r = _run_cli("--manifest", str(manifest), "--root", str(root))

    assert r.returncode == 2, (
        f"an unresolvable manifested path must exit 2, not {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "ERROR" in r.stderr, f"the error must reach stderr; stderr={r.stderr!r}"


def test_ac4_default_mode_errors_outrank_violations(tmp_path):
    """Ladder ORDER, not just the rungs: a tree carrying BOTH a real violation and
    an unresolvable path must exit 2 — the driver must not report the softer 1."""
    manifest, root = _tree(tmp_path, _DIRTY_MODULE)
    manifest.write_text(
        json.dumps({
            "core_modules": ["mod.py", "does_not_exist.py"],
            "host_modules": [],
            "forbidden": _FORBIDDEN,
        }),
        encoding="utf-8",
    )

    r = _run_cli("--manifest", str(manifest), "--root", str(root))

    assert r.returncode == 2, (
        f"errors must outrank violations (2 beats 1); got {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_ac5_default_mode_json_violation_still_exits_1(tmp_path):
    """--json must not soften the ladder: machine-readable output, same exit code."""
    manifest, root = _tree(tmp_path, _DIRTY_MODULE)

    r = _run_cli("--manifest", str(manifest), "--root", str(root), "--json")

    assert r.returncode == 1, f"--json must keep exit 1 on a violation; stdout={r.stdout!r}"
    payload = json.loads(r.stdout)
    assert payload["ok"] is False, f"JSON must report ok=False; payload={payload!r}"
    assert payload["violations"], f"JSON must carry the violation; payload={payload!r}"


def test_ac6_bad_command_line_exits_2_without_traceback():
    """argparse ladder: an unknown flag is a driver error (2), never 0, and never
    an escaping traceback that a caller would read as a real violation."""
    r = _run_cli("--no-such-flag")

    assert r.returncode == 2, f"a bad command line must exit 2; got {r.returncode}, stderr={r.stderr!r}"
    assert "Traceback" not in r.stderr, f"argparse failure must not escape as a traceback; stderr={r.stderr!r}"


def _write_pyproject(root: Path, py_modules: list[str] | None) -> Path:
    p = root / "pyproject.toml"
    if py_modules is None:
        p.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    else:
        listed = ", ".join(f'"{m}"' for m in py_modules)
        p.write_text(f"[tool.setuptools]\npy-modules = [{listed}]\n", encoding="utf-8")
    return p


def test_ac7_packaging_mode_clean_exits_0(tmp_path):
    """--packaging ladder, rung 0: py-modules matches the host-subtracted closure."""
    manifest, root = _tree(tmp_path, _CLEAN_MODULE)
    pyproject = _write_pyproject(root, ["mod"])

    r = _run_cli("--manifest", str(manifest), "--root", str(root),
                 "--packaging", "--pyproject", str(pyproject))

    assert r.returncode == 0, (
        f"a matching py-modules list must exit 0; got {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_ac8_packaging_mode_missing_pyproject_exits_2(tmp_path):
    """--packaging ladder, rung 2: an unreadable pyproject is a DRIVER error.

    §1n: ManifestError is caught and mapped to 2. Without that mapping the
    exception escapes and the process exits 1 — a traceback that reads exactly
    like a real packaging violation.
    """
    manifest, root = _tree(tmp_path, _CLEAN_MODULE)

    r = _run_cli("--manifest", str(manifest), "--root", str(root),
                 "--packaging", "--pyproject", str(root / "absent.toml"))

    assert r.returncode == 2, (
        f"an unreadable pyproject must exit 2, not {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "Traceback" not in r.stderr, f"must be a mapped error, not an escaping exception; stderr={r.stderr!r}"


@pytest.mark.parametrize(
    "py_modules, kind",
    [
        ([], "packaging_missing"),          # in the closure, undeclared → would not ship
        (["mod", "ghost"], "packaging_extra"),  # declared, outside the closure → ships host code
    ],
    ids=["missing", "extra"],
)
def test_ac10_packaging_mode_violation_exits_1_both_directions(tmp_path, py_modules, kind):
    """--packaging ladder, rung 1, BOTH directions of Rule R.

    Guards `return 1 -> return 0` in the packaging ladder. One direction alone
    would leave the mutation half-alive: a driver that only ever saw `missing`
    could still swallow `extra`, which is the direction that ships host code.
    """
    manifest, root = _tree(tmp_path, _CLEAN_MODULE)
    pyproject = _write_pyproject(root, py_modules)

    r = _run_cli("--manifest", str(manifest), "--root", str(root),
                 "--packaging", "--pyproject", str(pyproject))

    assert r.returncode == 1, (
        f"a {kind} packaging violation must exit 1, not {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert kind in r.stdout, f"the violation kind must be named; stdout={r.stdout!r}"


def test_ac11_packaging_mode_schema_error_outranks_violations(tmp_path):
    """--packaging ladder, rung 2 via schema errors — the last rung the CLI can
    reach without raising.

    A manifest that fails schema validation makes `lint_packaging` return
    `Result(errors=schema_errors)` rather than raise, so this exercises the
    `if result.errors: return 2` branch specifically (NOT the ManifestError
    mapping that AC8 covers). Fail-closed: a malformed manifest must never be
    reported as a mere violation.
    """
    manifest, root = _tree(tmp_path, _CLEAN_MODULE)
    pyproject = _write_pyproject(root, ["mod"])
    manifest.write_text(
        json.dumps({"core_modules": ["mod.py"], "host_modules": []}),  # no `forbidden`
        encoding="utf-8",
    )

    r = _run_cli("--manifest", str(manifest), "--root", str(root),
                 "--packaging", "--pyproject", str(pyproject))

    assert r.returncode == 2, (
        f"a schema-invalid manifest must exit 2, not {r.returncode} — "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "schema" in r.stderr, f"the schema error must be named on stderr; stderr={r.stderr!r}"


def test_ac9_real_repo_default_root_needs_no_flags(tmp_path):
    """§1l real-anchor: the SHIPPED manifest over the SHIPPED tree, invoked the way
    CI and a human would — no --manifest, no --root.

    bd#48 changed `_DEFAULT_ROOT` to the PACKAGE root (bd#44 makes manifest paths
    package-relative). With the pre-bd#48 default every path was unresolvable and
    this exits 2, so this AC pins the default, not just the flag-driven path.
    """
    r = _run_cli()

    assert r.returncode == 0, (
        f"the committed manifest must lint clean with NO flags; got {r.returncode} — "
        f"stdout={r.stdout[:600]!r} stderr={r.stderr[:600]!r}"
    )
