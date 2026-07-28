#!/usr/bin/env python3
"""version_parity.py — single canonical version + parity check (bd#99).

The repo carries several version declarations, registered in `DECLARATIONS`
below -- that list is the single source of truth for the set, and no count is
restated in prose. `engine_py/pyproject.toml` `[project].version` is canonical;
every other declaration must always agree with it.

Usage:
    version_parity.py [--root PATH] [--check]
    version_parity.py [--root PATH] --write X.Y.Z
    version_parity.py [--root PATH] --list-declarations

`--root` defaults to the process working directory. `--check` (the default
when no mode flag is given) compares every registered declaration against the
canonical value and lists every divergence. `--write X.Y.Z` sets all of them
to X.Y.Z, all-or-nothing. `--list-declarations` prints the
declaration registry as JSON.

Exit codes:
    0  — check/list-declarations succeeded, or write succeeded
    1  — check found problems, or write failed (invalid arg / unresolvable
         declaration) -- nothing is written in the failure case
    2  — argparse usage error (unknown flag, --check and --write together)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

KIND_TOML_PROJECT_VERSION = "toml-project-version"
KIND_JSON_FLAT = "json-flat"
KIND_JSON_NESTED = "json-nested"

DECLARATIONS = [
    ("engine_py/pyproject.toml", KIND_TOML_PROJECT_VERSION),
    ("package.json", KIND_JSON_FLAT),
    ("npm/package.json", KIND_JSON_FLAT),
    (".claude-plugin/plugin.json", KIND_JSON_FLAT),
    (".claude-plugin/marketplace.json", KIND_JSON_NESTED),
    ("packaging/pypi-pointer/pyproject.toml", KIND_TOML_PROJECT_VERSION),
]

CANONICAL_RELPATH = "engine_py/pyproject.toml"

# (relpath, required package name in its `[project].dependencies` pin) --
# exactly 2-tuples, a separate registry from DECLARATIONS (spec §2.1).
PIN_DECLARATIONS = [
    ("packaging/pypi-pointer/pyproject.toml", "bytedigger-engine"),
]

PIN_MAP = dict(PIN_DECLARATIONS)

_PROJECT_HEADER_RE = re.compile(r"^\[project\]\s*$")
_TABLE_HEADER_RE = re.compile(r"^\[")
_VERSION_LINE_RE = re.compile(r'^version\s*=\s*"([^"]+)"')
_DEP_KEY_RE = re.compile(r"^dependencies\s*=")


class DeclarationError(Exception):
    """Raised when a declaration cannot be resolved to a version value.

    `reason` is the fail-loud message (never a raw traceback); `path` is the
    repo-relative path the message must name.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason


def parse_project_version(text: str) -> str | None:
    """[project]-table-anchored canonical parse rule (spec §2.2/[G-10/T3]).

    Find the [project] table header, then the first `^version = "..."`
    line before the next `^[` table header. A decoy `version =` line in an
    earlier table (e.g. [build-system]) is never picked up.
    """
    in_project = False
    for line in text.splitlines():
        if _PROJECT_HEADER_RE.match(line):
            in_project = True
            continue
        if in_project:
            if _TABLE_HEADER_RE.match(line):
                break
            m = _VERSION_LINE_RE.match(line)
            if m:
                return m.group(1)
    return None


def _extract_project_dependencies_raw(text: str) -> str | None:
    """[project]-table-anchored scan (spec §2.2) for the raw text of the
    first `^dependencies\\s*=` line before the next `^[` table header,
    accumulating physical lines until a line containing `]` is seen (or
    EOF, if the value never terminates -- callers treat that as a grammar
    failure). Returns `None` if no such key is found in `[project]` at all.
    """
    in_project = False
    buf: str | None = None
    for line in text.splitlines():
        if _PROJECT_HEADER_RE.match(line):
            in_project = True
            continue
        if not in_project:
            continue
        if buf is None:
            if _TABLE_HEADER_RE.match(line):
                break
            if _DEP_KEY_RE.match(line):
                buf = line.split("=", 1)[1]
                if "]" in buf:
                    break
        else:
            buf += "\n" + line
            if "]" in line:
                break
    return buf


def _resolve_pin_entry(root: Path, relpath: str) -> str:
    """Resolve the single `dependencies` pin entry at `relpath` (spec §2.2
    grammar, including the string-array fence). Raises DeclarationError
    with the exact reasons of spec §2.4 -- caller decides what to compare
    it against."""
    path = root / relpath
    text = path.read_text()
    raw = _extract_project_dependencies_raw(text)
    if raw is None:
        raise DeclarationError(relpath, "no dependencies key")
    if "[" not in raw:
        raise DeclarationError(relpath, "dependencies is not an array of strings")
    start = raw.index("[")
    end = raw.rindex("]")
    body = raw[start + 1 : end]
    entries = re.findall(r'"([^"]*)"', body)
    remainder = re.sub(r'"[^"]*"', "", body)
    if not re.fullmatch(r"[\s,]*", remainder):
        raise DeclarationError(relpath, "dependencies is not an array of strings")
    if len(entries) != 1:
        raise DeclarationError(
            relpath, f"dependencies has {len(entries)} entries, expected exactly 1"
        )
    return entries[0]


def _check_pin(root: Path, relpath: str, pkg: str, canonical: str) -> None:
    """Pin equality check (spec §2.3): the single dependencies entry must
    equal `f"{pkg}=={canonical}"`, where `pkg` comes from PIN_DECLARATIONS
    (never from the entry itself -- spec AC30b)."""
    entry = _resolve_pin_entry(root, relpath)
    expected = f"{pkg}=={canonical}"
    if entry != expected:
        raise DeclarationError(relpath, f"pin is {entry}, expected {expected}")


def _surgical_write_pin(root: Path, relpath: str, pkg: str, old_version: str, new_version: str) -> None:
    """Position-anchored rewrite of the pin's version, mirroring
    `_surgical_write_toml`: scan within `[project]`'s `dependencies =`
    value (possibly spanning several physical lines) and replace only the
    exact quoted `pkg==old_version` snippet, leaving every other byte
    (comment prose, indentation, array layout) untouched (spec §2.5)."""
    path = root / relpath
    text = path.read_text()
    old_snippet = f'"{pkg}=={old_version}"'
    new_snippet = f'"{pkg}=={new_version}"'
    lines = text.splitlines(keepends=True)
    in_project = False
    in_deps = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if _PROJECT_HEADER_RE.match(stripped):
            in_project = True
            continue
        if not in_project:
            continue
        if not in_deps:
            if _TABLE_HEADER_RE.match(stripped):
                break
            if _DEP_KEY_RE.match(stripped):
                in_deps = True
                if old_snippet in line:
                    lines[i] = line.replace(old_snippet, new_snippet, 1)
                    break
                if "]" in stripped:
                    break
        else:
            if old_snippet in line:
                lines[i] = line.replace(old_snippet, new_snippet, 1)
                break
            if "]" in stripped:
                break
    path.write_text("".join(lines))


def _read_canonical(root: Path) -> str:
    """Read+parse the canonical version. Raises DeclarationError on any
    failure -- callers must treat this as an immediate-abort condition."""
    path = root / CANONICAL_RELPATH
    try:
        text = path.read_text()
    except OSError:
        raise DeclarationError(CANONICAL_RELPATH, "missing")
    version = parse_project_version(text)
    if version is None:
        raise DeclarationError(CANONICAL_RELPATH, "no version key")
    return version


def _resolve_flat_json(root: Path, relpath: str) -> str:
    path = root / relpath
    try:
        text = path.read_text()
    except OSError:
        raise DeclarationError(relpath, "missing")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DeclarationError(relpath, "unreadable")
    if not isinstance(data, dict):
        raise DeclarationError(relpath, "manifest is not a JSON object")
    if "version" not in data:
        raise DeclarationError(relpath, "no version key")
    version = data["version"]
    if not isinstance(version, str):
        raise DeclarationError(relpath, "version is not a string")
    if not VERSION_RE.match(version):
        raise DeclarationError(relpath, version)
    return version


def _resolve_nested_marketplace(root: Path, relpath: str) -> str:
    path = root / relpath
    try:
        text = path.read_text()
    except OSError:
        raise DeclarationError(relpath, "missing")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DeclarationError(relpath, "unreadable")
    if not isinstance(data, dict):
        raise DeclarationError(relpath, "manifest is not a JSON object")
    if "plugins" not in data:
        raise DeclarationError(relpath, "no plugins key")
    plugins = data["plugins"]
    if not isinstance(plugins, list):
        raise DeclarationError(relpath, "plugins is not a list")
    if len(plugins) == 0:
        raise DeclarationError(relpath, "plugins is empty")
    first = plugins[0]
    if not isinstance(first, dict):
        raise DeclarationError(relpath, "plugins[0] is not an object")
    if "version" not in first:
        raise DeclarationError(relpath, "plugins[0] has no version")
    version = first["version"]
    if not isinstance(version, str):
        raise DeclarationError(relpath, "plugins[0].version is not a string")
    if not VERSION_RE.match(version):
        raise DeclarationError(relpath, version)
    return version


def _resolve(root: Path, relpath: str, kind: str) -> str:
    if kind == KIND_TOML_PROJECT_VERSION:
        text = (root / relpath).read_text()
        version = parse_project_version(text)
        if version is None:
            raise DeclarationError(relpath, "no version key")
        return version
    if kind == KIND_JSON_FLAT:
        return _resolve_flat_json(root, relpath)
    if kind == KIND_JSON_NESTED:
        return _resolve_nested_marketplace(root, relpath)
    raise AssertionError(f"unknown declaration kind: {kind}")


def cmd_check(root: Path) -> int:
    try:
        canonical = _read_canonical(root)
    except DeclarationError as e:
        print(f"{e.path}: {e.reason}")
        return 1

    problems: list[str] = []
    for relpath, kind in DECLARATIONS:
        if relpath == CANONICAL_RELPATH:
            continue
        try:
            path = root / relpath
            if not path.exists():
                raise DeclarationError(relpath, "missing")
            version = _resolve(root, relpath, kind)
        except DeclarationError as e:
            problems.append(f"{e.path}: {e.reason}")
            continue
        if version != canonical:
            problems.append(f"{relpath}: found {version}, expected {canonical}")
        pin_pkg = PIN_MAP.get(relpath)
        if pin_pkg is not None:
            try:
                _check_pin(root, relpath, pin_pkg, canonical)
            except DeclarationError as e:
                problems.append(f"{e.path}: {e.reason}")

    if problems:
        for line in problems:
            print(line)
        return 1

    print(f"OK: {len(DECLARATIONS)} declarations match {canonical}")
    return 0


def _validate_version_arg(value: str) -> bool:
    return bool(VERSION_RE.match(value))


def _surgical_write_flat(root: Path, relpath: str, old_version: str, new_version: str) -> None:
    path = root / relpath
    text = path.read_text()
    old_snippet = f'"version": "{old_version}"'
    new_snippet = f'"version": "{new_version}"'
    path.write_text(text.replace(old_snippet, new_snippet, 1))


def _surgical_write_nested(root: Path, relpath: str, old_version: str, new_version: str) -> None:
    path = root / relpath
    text = path.read_text()
    old_snippet = f'"version": "{old_version}"'
    new_snippet = f'"version": "{new_version}"'
    path.write_text(text.replace(old_snippet, new_snippet, 1))


def _surgical_write_toml(root: Path, relpath: str, old_version: str, new_version: str) -> None:
    path = root / relpath
    text = path.read_text()
    old_snippet = f'version = "{old_version}"'
    new_snippet = f'version = "{new_version}"'
    # Replace only the FIRST occurrence at/after the [project] header, so a
    # decoy version elsewhere (e.g. [build-system]) is never touched.
    lines = text.splitlines(keepends=True)
    in_project = False
    for i, line in enumerate(lines):
        if _PROJECT_HEADER_RE.match(line.rstrip("\n")):
            in_project = True
            continue
        if in_project:
            if _TABLE_HEADER_RE.match(line.rstrip("\n")):
                break
            if _VERSION_LINE_RE.match(line.rstrip("\n")) and old_snippet in line:
                lines[i] = line.replace(old_snippet, new_snippet, 1)
                break
    path.write_text("".join(lines))


def cmd_write(root: Path, new_version: str) -> int:
    if not _validate_version_arg(new_version):
        print(f"invalid version argument: {new_version!r} (expected X.Y.Z)")
        return 1

    resolved: dict[str, str] = {}
    resolved_pins: dict[str, tuple[str, str]] = {}
    for relpath, kind in DECLARATIONS:
        try:
            path = root / relpath
            if not path.exists():
                raise DeclarationError(relpath, "missing")
            version = _resolve(root, relpath, kind)
            pin_pkg = PIN_MAP.get(relpath)
            if pin_pkg is not None:
                entry = _resolve_pin_entry(root, relpath)
                if "==" in entry:
                    entry_pkg, entry_version = entry.split("==", 1)
                else:
                    entry_pkg, entry_version = entry, ""
                resolved_pins[relpath] = (entry_pkg, entry_version)
        except DeclarationError as e:
            print(f"{e.path}: {e.reason}")
            return 1
        resolved[relpath] = version

    for relpath, kind in DECLARATIONS:
        old_version = resolved[relpath]
        if kind == KIND_TOML_PROJECT_VERSION:
            _surgical_write_toml(root, relpath, old_version, new_version)
        elif kind == KIND_JSON_FLAT:
            _surgical_write_flat(root, relpath, old_version, new_version)
        elif kind == KIND_JSON_NESTED:
            _surgical_write_nested(root, relpath, old_version, new_version)
        if relpath in resolved_pins:
            entry_pkg, entry_old_version = resolved_pins[relpath]
            _surgical_write_pin(root, relpath, entry_pkg, entry_old_version, new_version)

    print(f"OK: wrote {new_version} to {len(DECLARATIONS)} declarations")
    return 0


def cmd_list_declarations() -> int:
    registry = [{"path": relpath, "kind": kind} for relpath, kind in DECLARATIONS]
    print(json.dumps(registry))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check or bump the version across every registered declaration site, "
            "canonical = engine_py/pyproject.toml [project].version. "
            "Argparse usage errors (unknown flag, --check+--write together) "
            "exit 2 -- argparse's own behaviour."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repo root to resolve declarations against (default: cwd).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check every registered declaration (default).")
    mode.add_argument("--write", metavar="X.Y.Z", help="Set every registered declaration to X.Y.Z.")
    mode.add_argument(
        "--list-declarations",
        action="store_true",
        help="Print the declaration registry as JSON.",
    )
    args = parser.parse_args()

    root = args.root.resolve()

    if args.list_declarations:
        return cmd_list_declarations()
    if args.write is not None:
        return cmd_write(root, args.write)
    return cmd_check(root)


if __name__ == "__main__":
    sys.exit(main())
