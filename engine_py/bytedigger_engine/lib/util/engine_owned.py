"""Engine-owned paths (bd#94): the one predicate that says "this path is engine state".

Engine state (the host state dir, see ``config_provider.foreign_state_dirname``) is not the
user's or the agent's change. Every path-list producer that feeds a gate (scan sets, commit
manifests, dirty checks, ``git add`` pathspecs, tree walkers) consumes this module, so engine
state never reaches a scanner, a manifest or a commit.

Two rules:

* R1 (state dir): any segment of the root-relative path equals the state dirname.
* R2 (read sets only, ``content_scan=True``): the path, or a parent component below the repo
  root, is a symlink that escapes the root, dangles, loops, or resolves INTO the state dir (R2b).

This module reads no file content, spawns no process and runs no git: it holds no class-I
primitive. Stdlib plus ``config_provider`` and ``telemetry_ctx`` only. Python 3.9 compatible.
"""
from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Any, Iterable, List, Optional

from bytedigger_engine import config_provider, telemetry_ctx

EVENT_TYPE = "engine_owned_paths_dropped"
_DEFAULT_DIRNAME = ".bytedigger"
_EVENT_PATH_CAP = 20


def engine_state_dirname() -> str:
    """State dirname, read at call time; degrades to ``.bytedigger``; never raises."""
    try:
        name = config_provider.foreign_state_dirname()
    except Exception:  # noqa: BLE001
        return _DEFAULT_DIRNAME
    if (not isinstance(name, str) or not name or "/" in name or "\\" in name
            or name in (".", "..")):
        return _DEFAULT_DIRNAME
    return name


def _under(path: str, root: str) -> Optional[str]:
    """Lexical remainder of *path* below *root* ('.' when equal), or None."""
    if path == root:
        return "."
    prefix = root if root.endswith("/") else root + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return None


def _resolve_str(p: str) -> str:
    try:
        return str(Path(p).resolve())
    except (OSError, RuntimeError, ValueError):
        return p


def _root_relative(path: str, repo_root: str) -> Optional[str]:
    """Root-relative form of *path* (already ``/``-normalised), or None when absolute and outside."""
    if not posixpath.isabs(path):
        return path
    root = posixpath.normpath(repo_root.replace("\\", "/")) if repo_root else ""
    if root:
        rel = _under(path, root)
        if rel is not None:
            return rel
    root_resolved = _resolve_str(repo_root or ".").replace("\\", "/")
    path_resolved = _resolve_str(path).replace("\\", "/")
    candidates = [(path_resolved, root_resolved), (path, root_resolved)]
    if root:
        candidates.append((path_resolved, root))
    for p, r in candidates:
        rel = _under(p, r)
        if rel is not None:
            return rel
    return None


def _normalised(path: Any, repo_root: Any) -> Optional[str]:
    """Normalised root-relative path, or None (empty, absolute outside root, or escaping with ``..``)."""
    try:
        raw = os.fspath(path)
        root = os.fspath(repo_root)
    except TypeError:
        return None
    if not isinstance(raw, str) or not isinstance(root, str) or not raw.strip():
        return None
    rel = _root_relative(raw.replace("\\", "/"), root)
    if rel is None:
        return None
    norm = posixpath.normpath(rel)
    if norm == ".." or norm.startswith("../"):
        return None
    return norm


def _has_state_segment(norm: str, dirname: str) -> bool:
    return any(seg == dirname for seg in norm.split("/"))


def is_engine_state_path(path, repo_root) -> bool:
    """R1 only: some root-relative segment equals the state dirname. Never raises."""
    try:
        norm = _normalised(path, repo_root)
        if norm is None:
            return False
        return _has_state_segment(norm, engine_state_dirname())
    except Exception:  # noqa: BLE001
        return False


def _inside(real: str, root_real: str) -> bool:
    return real == root_real or real.startswith(root_real.rstrip(os.sep) + os.sep)


def _is_escaping_link(norm: str, repo_root: str, dirname: str) -> bool:
    """R2 / R2b over the components of *norm* below the root."""
    root_real = os.path.realpath(repo_root)
    cur = repo_root
    for seg in norm.split("/"):
        if seg in ("", "."):
            continue
        cur = os.path.join(cur, seg)
        if not os.path.islink(cur):
            continue
        if not os.path.exists(cur):
            return True  # dangling or looping link: not user content to read
        real = os.path.realpath(cur)
        if not _inside(real, root_real):
            return True
        rel = os.path.relpath(real, root_real).replace(os.sep, "/")
        if rel != "." and _has_state_segment(rel, dirname):
            return True  # R2b: decoy link into the state dir
    return False


def is_engine_owned_path(path, repo_root, *, content_scan: bool) -> bool:
    """R1, or (``content_scan=True``) R1 or R2. Never raises."""
    try:
        norm = _normalised(path, repo_root)
        if norm is None:
            return False
        dirname = engine_state_dirname()
        if _has_state_segment(norm, dirname):
            return True
        if not content_scan:
            return False
        try:
            return _is_escaping_link(norm, os.fspath(repo_root), dirname)
        except (OSError, RuntimeError, ValueError):
            return True  # resolution failure in a content scan: degrade to dropped
    except Exception:  # noqa: BLE001
        return False


def _emit_dropped(step: str, dropped: List[str], content_scan: bool) -> None:
    payload = {
        "step": step,
        "n_dropped": len(dropped),
        "content_scan": content_scan,
        "paths": sorted(dropped)[:_EVENT_PATH_CAP],
    }
    try:
        telemetry_ctx.emit_safe(EVENT_TYPE, payload)
    except Exception:  # noqa: BLE001
        pass


def drop_engine_owned(paths: Iterable, repo_root, *, step: str, content_scan: bool) -> List[str]:
    """Order-preserving filter by ``is_engine_owned_path``; one event when anything is dropped."""
    kept: List[str] = []
    dropped: List[str] = []
    for p in paths:
        if is_engine_owned_path(p, repo_root, content_scan=content_scan):
            try:
                dropped.append(os.fspath(p))
            except TypeError:
                dropped.append(str(p))
        else:
            kept.append(p)
    if dropped:
        _emit_dropped(step, dropped, content_scan)
    return kept


def engine_owned_pathspecs() -> List[str]:
    """Git exclude pathspecs for the state dir at the root and at any depth."""
    d = engine_state_dirname()
    return [f":(exclude){d}", f":(exclude)**/{d}/**"]


def prune_engine_owned_dirs(dirnames: List[str]) -> None:
    """Remove the state dirname from *dirnames* in place (``os.walk`` pruning)."""
    d = engine_state_dirname()
    dirnames[:] = [n for n in dirnames if n != d]


_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def _c_unquote(body: str) -> str:
    out = bytearray()
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch != "\\" or i + 1 >= n:
            out.extend(ch.encode("utf-8"))
            i += 1
            continue
        nxt = body[i + 1]
        if nxt in "01234567":
            j = i + 1
            digits = ""
            while j < n and len(digits) < 3 and body[j] in "01234567":
                digits += body[j]
                j += 1
            out.append(int(digits, 8) & 0xFF)
            i = j
        elif nxt in _C_ESCAPES:
            out.append(_C_ESCAPES[nxt])
            i += 2
        else:
            out.extend(("\\" + nxt).encode("utf-8"))
            i += 2
    return out.decode("utf-8", errors="replace")


def porcelain_path(line: str) -> str:
    """Path of a ``git status --porcelain`` v1 line (rename target, C-unquoted). Never raises."""
    try:
        if not isinstance(line, str) or len(line) < 4:
            return ""
        field = line[3:]
        if (line[0] in "RC" or line[1] in "RC") and " -> " in field:
            field = field.rpartition(" -> ")[2]
        if len(field) >= 2 and field.startswith('"') and field.endswith('"'):
            field = _c_unquote(field[1:-1])
        return field
    except Exception:  # noqa: BLE001
        return ""


def drop_engine_owned_porcelain(lines: Iterable[str], repo_root, *, step: str) -> List[str]:
    """Drop porcelain lines whose path is engine state (R1), plus blank lines; one event if any state dropped."""
    kept: List[str] = []
    dropped: List[str] = []
    for ln in lines:
        if not isinstance(ln, str) or not ln.strip():
            continue
        path = porcelain_path(ln)
        if is_engine_state_path(path, repo_root):
            dropped.append(path)
        else:
            kept.append(ln)
    if dropped:
        _emit_dropped(step, dropped, False)
    return kept
