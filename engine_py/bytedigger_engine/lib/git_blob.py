"""git_blob — tri-state read of one file at one revision (bd#117 Part A).

``read_blob(repo, rev, path)`` returns exactly one of:

    ("ok", <bytes>)        the file exists at ``rev`` and was read
    ("absent", None)       ``rev`` resolves, the file is not in its tree
    ("error", <detail>)    ``rev`` does not resolve, git failed, or it timed out

"Absent" and "error" are kept apart on purpose: a caller that treats a git
failure as "no file" would fail open. Stdlib only; git runs with cwd=``repo``.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from bytedigger_engine.lib.bounded_spawn import TIMEOUT_RETURNCODE, bounded_run

_TIMEOUT_S = 30


def _git(
    repo: Path, args: list[str], env: Mapping[str, str] | None
) -> tuple[int, bytes, str]:
    try:
        proc = bounded_run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except OSError as exc:
        return 127, b"", str(exc)
    if proc.returncode == TIMEOUT_RETURNCODE:
        return TIMEOUT_RETURNCODE, b"", f"git {args[0]}: timeout after {_TIMEOUT_S}s"
    err = proc.stderr.decode("utf-8", "replace").strip() if proc.stderr else ""
    return proc.returncode, proc.stdout or b"", err


def read_blob(
    repo: str | Path,
    rev: str,
    path: str,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, bytes | str | None]:
    """Read ``path`` at ``rev`` in ``repo``; see the module docstring for the tri-state."""
    root = Path(repo)
    rc, _, err = _git(root, ["rev-parse", "--verify", f"{rev}^{{tree}}"], env)
    if rc != 0:
        return "error", err or f"cannot resolve revision {rev!r} (git rc={rc})"
    rc, out, err = _git(root, ["ls-tree", "--name-only", rev, "--", path], env)
    if rc != 0:
        return "error", err or f"git ls-tree rc={rc}"
    if not out.strip():
        return "absent", None
    rc, out, err = _git(root, ["cat-file", "blob", f"{rev}:{path}"], env)
    if rc != 0:
        return "error", err or f"git cat-file rc={rc}"
    return "ok", out
