"""RED — package_meta.EXTRA_* constants must pin 1:1 with pyproject.toml
`[project.optional-dependencies]`, in BOTH directions (package_meta.py
docstring: "The extras below must stay in sync with `pyproject.toml`
`[project.optional-dependencies]` -- AC9/AC10 pin both directions"). Grepped:
no such test exists today (test_engine_path_closure.py:331 reads extras for
import-closure names, not for this parity; test_gh898_backend_pip_hint.py
only compares install_hint output). This file is that missing pin (BD21).

Both extra-name sets below are derived programmatically at test-assert time
(sec 1q -- nothing resolves at import time), never hardcoded:
  - package_meta side: introspect the module for EXTRA_* attributes/values.
  - pyproject side: parse the TOML file, same idiom as
    engine_py/tests/test_engine_path_closure.py's `_declared_deps()`
    (tomllib on py3.11+, tomli fallback -- a failure there is a real
    failure, not a skip).

conftest.py (this dir) puts engine_py root on sys.path at import time
(conftest-import-time singleton, sec 1q) -- package_meta is imported inside
each test body, never at module level, so collection succeeds either way.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_PY_ROOT = Path(__file__).resolve().parent.parent


def _package_meta_extras():
    """name -> value for every EXTRA_* string constant package_meta.py defines."""
    from bytedigger_engine import package_meta

    return {
        name: value
        for name, value in vars(package_meta).items()
        if name.startswith("EXTRA_") and isinstance(value, str)
    }


def _pyproject_extra_keys():
    """Keys of [project.optional-dependencies] in engine_py/pyproject.toml,
    parsed fresh at assert time -- never transcribed."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    with (ENGINE_PY_ROOT / "pyproject.toml").open("rb") as f:
        cfg = tomllib.load(f)
    return set(cfg["project"].get("optional-dependencies", {}).keys())


class TestExtrasParityConstantsDeclaredInPyproject:
    """AC1 / direction 1: every EXTRA_* constant in package_meta.py names a
    key that pyproject.toml actually declares under
    [project.optional-dependencies]."""

    def test_every_extra_constant_is_declared_in_pyproject(self):
        constants = _package_meta_extras()
        declared = _pyproject_extra_keys()
        missing = {
            f"{name}={value!r}"
            for name, value in constants.items()
            if value not in declared
        }
        assert not missing, (
            "package_meta.py declares EXTRA_* constants with no matching "
            "[project.optional-dependencies] key in pyproject.toml: "
            + ", ".join(sorted(missing))
            + f"\nDeclared pyproject extras: {sorted(declared)}"
        )


class TestExtrasParityPyprojectKeysHaveConstants:
    """AC2 / direction 2: every [project.optional-dependencies] key in
    pyproject.toml has a matching EXTRA_* constant in package_meta.py."""

    def test_every_pyproject_extra_has_a_constant(self):
        constants = _package_meta_extras()
        declared = _pyproject_extra_keys()
        constant_values = set(constants.values())
        missing = declared - constant_values
        assert not missing, (
            "pyproject.toml [project.optional-dependencies] declares extras "
            "with no matching EXTRA_* constant in package_meta.py: "
            + ", ".join(sorted(missing))
            + f"\npackage_meta EXTRA_* values: {sorted(constant_values)}"
        )
