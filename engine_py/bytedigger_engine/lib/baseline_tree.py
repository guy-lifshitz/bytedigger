"""baseline_tree — GH1612-B: the ONE canonical baseline-tree provider (D1).

A context manager yielding the path of a detached `git worktree` checked out
at *ref*, or `None` when it could not be created. ALWAYS removes the worktree
and its temp parent directory — success, failure or exception. Never touches
the live checkout and never uses `refs/stash`: the class this ships closes is
"a baseline is measured on a tree obtained by MUTATING the live checkout"
(hal#1612 front matter).

Extracted from the two copy-pasted implementations that already existed in
production (`phase_6_review.py` / `phase_8_post_deploy.py`, §1g) — this is
the single source both are re-pointed at (D3), plus the new consumer this
lot builds (`phase_5_implement._compute_baseline_typecheck_count`, D2).

The git port is a MANDATORY keyword parameter, never imported here and never
given a default value (D1 / gate MAJOR-2): the caller resolves its own port
from ITS module globals AT CALL TIME — e.g. `baseline_tree(..., _git_write=
_git_write)` written literally at the call site inside the caller's own
function body — so a test that monkeypatches the CALLER's module attribute
(`monkeypatch.setattr(phase_6_review, "_git_write", ...)`) still reaches the
real seam. A provider that imported the port itself, or captured it as a
function default, would sever exactly that seam (test_phase_6_post_fix_
typecheck_gate_GH316.py's `status="ok"` expectation still holds even when
severed, because a severed seam degrades to `None` — see AC14).

Port shape (matches `phase_workflows_common._git_write` and `phase_8_post_
deploy._git_write`, both already in production):
    _git_write(argv: list[str], cwd: str | Path, *, timeout: int = 30)
        -> tuple[int, str, str]   # (returncode, stdout, stderr)

`worktree add` rc != 0 conforms to the house degradation already shipped by
both existing copies (`phase_6_review.py`, `phase_8_post_deploy.py`): never a
raise, never a new error code — the caller reads `None` and degrades exactly
as it does today. `on_unavailable`, when given, is called with the raw
returncode BEFORE yielding `None`, so the caller can emit its own named event
through ITS OWN emit port (`phase_5_implement._emit_safe`) — this provider
never emits anything itself, so a caller's `monkeypatch.setattr(module,
"_emit_safe", ...)` always sees the event (Names pinned table).

`tempfile.mkdtemp` is reached through the `tempfile` MODULE at call time
(plain `import tempfile` + `tempfile.mkdtemp(...)`, never `from tempfile
import mkdtemp`) so a test wrapping `tempfile.mkdtemp` (AC17) sees every
allocation. Prefix `hal_baseline_tree_` (Names pinned table) scopes that
check to this provider's own allocations.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Iterator, Optional

# Port shape: (argv, cwd, *, timeout=30) -> (returncode, stdout, stderr).
_GitWritePort = Callable[..., tuple[int, str, str]]

_TEMP_PREFIX = "hal_baseline_tree_"

# Module logger for cleanup-failure reporting (finally-block, never raises).
_logger = logging.getLogger(__name__)


@contextlib.contextmanager
def baseline_tree(
    *,
    ref: str,
    git_cwd: str | Path,
    _git_write: _GitWritePort,
    on_unavailable: Optional[Callable[[int], None]] = None,
) -> Iterator[str | None]:
    """Yield the path of a detached worktree at *ref*, or `None`.

    ALWAYS removes the worktree and its temp parent — success, failure or
    exception. Never touches the live checkout (`git_cwd` is only ever
    passed as the `cwd` a `worktree add`/`worktree remove` runs FROM, never
    written to), never uses `refs/stash`.
    """
    parent = tempfile.mkdtemp(prefix=_TEMP_PREFIX)
    wt = os.path.join(parent, "wt")
    worktree_added = False
    try:
        rc, _, _ = _git_write(["worktree", "add", "--detach", wt, ref], git_cwd)
        if rc != 0:
            if on_unavailable is not None:
                on_unavailable(rc)
            yield None
            return
        worktree_added = True
        yield wt
    finally:
        if worktree_added:
            try:
                _git_write(["worktree", "remove", "--force", wt], git_cwd)
            except Exception:
                # Cleanup must never raise out of `finally` (it must not mask
                # the caller's own verdict/exception) — but it must not be
                # silent either: report at debug with the traceback so a
                # leaked `.git/worktrees` registration is at least visible.
                _logger.debug(
                    "baseline_tree: worktree remove failed for %s", wt, exc_info=True
                )
        # `ignore_errors=True` already swallows failures without raising;
        # the extra try/except here was redundant.
        shutil.rmtree(parent, ignore_errors=True)
