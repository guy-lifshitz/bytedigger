"""RED-side test integrity (bd#226): deleted test files, removed tests, added skips.

Pure + git read only (via ``git_port.git_read``). The caller supplies the
exemption predicates; this module never reads the spec or the pragma itself.

Spec: docs/decisions/2026-10-03-bd-red-test-integrity.md
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bytedigger_engine.lib import git_port
from bytedigger_engine.lib.util.path_classifier import _is_test_path

_GIT_TIMEOUT = 30

# Test definitions: one regex per language, per line.
_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_JS_DEF = re.compile(r"""^\s*(?:it|test)\s*\(\s*(['"`])(.+?)\1""")
_GO_DEF = re.compile(r"^func\s+(Test\w*)\s*\(")
_BATS_DEF = re.compile(r"""^@test\s+(['"])(.+?)\1""")

# Skip markers (one count per line at most).
_SKIP_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"pytest\.mark\.skip",
        r"pytest\.mark\.xfail",
        r"pytest\.skip\(",
        r"unittest\.skip",
        r"\.skip\(",
        r"\bxit\(",
        r"\bxdescribe\(",
        r"t\.Skip\(",
        r"^\s*skip(?:\s|$)",
    )
)


def _defined_names(text: str) -> "set[str]":
    names: "set[str]" = set()
    for line in text.splitlines():
        m = _PY_DEF.match(line) or _GO_DEF.match(line)
        if m:
            names.add(m.group(1))
            continue
        m = _JS_DEF.match(line) or _BATS_DEF.match(line)
        if m:
            names.add(m.group(2))
    return names


def _skip_count(text: str) -> int:
    n = 0
    for line in text.splitlines():
        if any(p.search(line) for p in _SKIP_PATTERNS):
            n += 1
    return n


def _empty(skip_reason: "str | None") -> "dict[str, Any]":
    return {
        "deleted_files": [],
        "removed_tests": [],
        "added_skips": [],
        "exempted": [],
        "skip_reason": skip_reason,
    }


def compute_test_integrity(
    base_sha: str,
    git_cwd: str,
    *,
    is_authorized: Callable[[str], bool],
    has_pragma: Callable[[str], bool],
) -> "dict[str, Any]":
    """Return non-exempt integrity findings of the worktree vs ``base_sha``.

    Keys: ``deleted_files`` (list[str]), ``removed_tests`` (list of
    ``{path, names}``), ``added_skips`` (list of ``{path, n}``), ``exempted``
    (list of ``{kind, path, ...}`` entries skipped by the callbacks) and
    ``skip_reason`` (None, or why detection was skipped -- fail-open).
    """
    if not base_sha:
        return _empty("no_base_sha")
    try:
        diff = git_port.git_read(
            ["diff", "--name-status", "--no-renames", "-z", base_sha],
            cwd=git_cwd, timeout=_GIT_TIMEOUT,
        )
        if diff.returncode != 0:
            return _empty("diff_failed")
        toks = diff.stdout.split("\0")
        entries: "list[tuple[str, str]]" = []
        i = 0
        while i + 1 < len(toks):
            status, path = toks[i], toks[i + 1]
            i += 2
            if status and path and _is_test_path(path):
                entries.append((status[0], path))

        untracked = git_port.git_read(
            ["ls-files", "--others", "--exclude-standard", "-z"],
            cwd=git_cwd, timeout=_GIT_TIMEOUT,
        )
        if untracked.returncode != 0:
            return _empty("untracked_failed")
        untracked_tests = [p for p in untracked.stdout.split("\0") if p and _is_test_path(p)]

        root = Path(git_cwd)

        def _post(path: str) -> "str | None":
            try:
                return (root / path).read_text(errors="replace")
            except OSError:
                return None

        # Names defined in ANY post-RED test file among the changed set.
        post_text: "dict[str, str]" = {}
        post_names: "set[str]" = set()
        for st, path in entries + [("?", p) for p in untracked_tests]:
            if st == "D":
                continue
            text = _post(path)
            if text is None:
                continue
            post_text[path] = text
            post_names |= _defined_names(text)

        out = _empty(None)
        for st, path in sorted(entries, key=lambda e: e[1]):
            if st == "D":
                if is_authorized(path):
                    out["exempted"].append({"kind": "deleted_file", "path": path})
                else:
                    out["deleted_files"].append(path)
                continue
            if st != "M":
                continue
            post = post_text.get(path)
            if post is None:
                continue
            show = git_port.git_read(
                ["show", f"{base_sha}:{path}"], cwd=git_cwd, timeout=_GIT_TIMEOUT,
            )
            if show.returncode != 0:
                continue
            removed = sorted(_defined_names(show.stdout) - post_names)
            added = _skip_count(post) - _skip_count(show.stdout)
            if not removed and added <= 0:
                continue
            if has_pragma(path):
                if removed:
                    out["exempted"].append({"kind": "removed_tests", "path": path, "names": removed})
                if added > 0:
                    out["exempted"].append({"kind": "added_skips", "path": path, "n": added})
                continue
            if removed:
                out["removed_tests"].append({"path": path, "names": removed})
            if added > 0:
                out["added_skips"].append({"path": path, "n": added})
        return out
    except Exception:
        return _empty("integrity_failed")
