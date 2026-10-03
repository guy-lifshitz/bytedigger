#!/usr/bin/env python3
"""bd#89 P3d: every rollout flag in the flags catalog needs an owner and provenance.

Rollout set (see is_rollout): a dict entry of kind flag/gate whose name ends
_ENFORCE, or whose description carries a flip-by:/retire-by:/kill-by: token, or
which already carries owner/provenance; plus the names in _EXTRA_ROLLOUT.

Rule per rollout entry:
  - owner: a non-blank str.
  - provenance: a str whose stripped value starts with "introduced:" and has
    non-blank text after it.

No date logic: expiry belongs to scripts/flip_horizon.py and its ledger.

Exit codes: 0 clean (prints "OK: N rollout flags"), 1 violations (one stderr
line each, starting with the flag name), 2 unreadable/unloadable catalog, no
FLAGS, FLAGS not a dict, or an import-time exception (one stderr line).

Usage: python3 scripts/flag_owner_lint.py [--catalog PATH]
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO_ROOT / "engine_py" / "bytedigger_engine" / "flags_catalog.py"

_ROLLOUT_KINDS = ("flag", "gate")
_DATED_TOKENS = ("flip-by:", "retire-by:", "kill-by:")
_EXTRA_ROLLOUT = frozenset({"HAL_SIBLING_AUDIT_GATE"})
_PROV_PREFIX = "introduced:"


class CatalogError(Exception):
    """The catalog cannot be read or loaded (exit 2)."""


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def load_flags(path) -> dict:
    """Execute the catalog file by path and return its FLAGS dict."""
    path = Path(path)
    if not path.is_file():
        raise CatalogError(f"cannot read catalog: {path}")
    old_flag = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("flag_owner_lint_catalog", path)
        if spec is None or spec.loader is None:
            raise CatalogError(f"cannot load catalog: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except CatalogError:
        raise
    except (Exception, SystemExit) as exc:
        raise CatalogError(
            f"cannot load catalog {path}: {type(exc).__name__}: {_one_line(exc)}"
        ) from None
    finally:
        sys.dont_write_bytecode = old_flag
    if not hasattr(mod, "FLAGS"):
        raise CatalogError(f"catalog has no FLAGS: {path}")
    flags = mod.FLAGS
    if not isinstance(flags, dict):
        raise CatalogError(f"FLAGS is not a dict: {path}")
    return flags


def is_rollout(name: str, entry) -> bool:
    """True when the entry belongs to the rollout set."""
    if not isinstance(entry, dict):
        return False
    if name in _EXTRA_ROLLOUT:
        return True
    if entry.get("kind") not in _ROLLOUT_KINDS:
        return False
    if name.endswith("_ENFORCE"):
        return True
    desc = entry.get("description")
    if isinstance(desc, str) and any(tok in desc for tok in _DATED_TOKENS):
        return True
    return "owner" in entry or "provenance" in entry


def _entry_violations(name: str, entry: dict) -> list:
    out = []
    owner = entry.get("owner")
    if not (isinstance(owner, str) and owner.strip()):
        out.append(f"{name}: owner missing or not a non-blank string")
    prov = entry.get("provenance")
    if not isinstance(prov, str):
        out.append(f"{name}: provenance missing or not a string")
    else:
        stripped = prov.strip()
        if not (
            stripped.startswith(_PROV_PREFIX)
            and stripped[len(_PROV_PREFIX):].strip()
        ):
            out.append(
                f"{name}: provenance must start with 'introduced:' followed by text"
            )
    return out


def lint(flags: dict) -> tuple:
    """Return (rollout_count, violations) for a FLAGS dict."""
    violations = []
    count = 0
    for name, entry in flags.items():
        name = str(name)
        if not isinstance(entry, dict):
            if name in _EXTRA_ROLLOUT or name.endswith("_ENFORCE"):
                violations.append(f"{name}: entry is not a dict")
            continue
        if not is_rollout(name, entry):
            continue
        count += 1
        violations.extend(_entry_violations(name, entry))
    return count, violations


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="path to flags_catalog.py")
    args = parser.parse_args(argv)
    try:
        flags = load_flags(args.catalog)
    except CatalogError as exc:
        print(_one_line(exc), file=sys.stderr)
        return 2
    count, violations = lint(flags)
    if violations:
        for line in violations:
            print(line, file=sys.stderr)
        return 1
    print(f"OK: {count} rollout flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
