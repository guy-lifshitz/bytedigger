"""Layout-agnostic tree-root resolution (bd#97).

Engine modules ship in trees of very different depth: the upstream HAL build
tree (``~/.claude/SYSTEM/cli/build/engine_py/``, 9 ancestors above a
``workflows/`` module), a bytedigger clone or worktree, a ``git archive``
tarball with no ``.git`` at all, and an installed wheel in site-packages. A
fixed ``Path(__file__).resolve().parents[N]`` is correct in at most one of
those: it raises ``IndexError`` in the shallow ones and silently resolves to an
unrelated directory in the others.

That is not hypothetical. ``workflows/phase_6_smoke.py`` bound ``parents[5]`` at
import time, so on a clean clone the import of ``workflows/__init__.py`` died
and took all 16 test modules with it (bd#97) -- while the same line was
*correct* upstream, which is why no ancestor count could fix it.

Resolve by what the tree CONTAINS, never by counting ancestors.

Note on ``from __future__ import annotations`` above: it is required, not
stylistic. ``pyproject.toml`` declares ``requires-python = ">=3.9"``, and a
PEP-604 union (``str | None``) in a signature is evaluated at
function-definition time, so without the future import this module would raise
``TypeError`` at import on 3.9 -- reproducing bd#97's exact blast radius, since
this module sits inside ``workflows/__init__.py``'s import closure.
"""
from __future__ import annotations

from pathlib import Path


def resolve_tree_root(start: Path, marker: str | None = None) -> Path:
    """Best available root for ``start``. Never raises, at any depth.

    Resolution order:

    1. ``marker`` given and some ancestor carries it -> that ancestor. The only
       signal that identifies the tree an asset actually lives in, so it
       outranks ``.git``: a submodule or nested checkout *below* the real root
       would otherwise win and yield a root that does not carry the asset.
    2. Nearest ancestor holding ``.git`` -> a checkout root. Tested with
       ``.exists()``, not ``.is_dir()``, because a git worktree's ``.git`` is a
       *file* containing a ``gitdir:`` pointer.
    3. ``ancestors[1]``, clamped to ``ancestors[-1]``; ``start`` itself when
       there are no ancestors at all. Nothing here can ``IndexError``.

    Branch 3 precondition: ``ancestors[1]`` is "the package root" only for a
    caller at ``<root>/engine_py/<subdir>/module.py`` -- which is true of every
    call site today. A caller directly at ``engine_py/module.py`` would get the
    directory *above* ``engine_py`` instead. Pass a ``marker`` if that matters.

    No environment variable is consulted. An ambient install-root override
    would let a value that does not carry ``marker`` win over an ancestor that
    does, breaking exactly the guarantee branch 1 exists to provide.
    """
    ancestors = list(start.parents)

    if marker:
        for ancestor in ancestors:
            if (ancestor / marker).exists():
                return ancestor

    for ancestor in ancestors:
        if (ancestor / ".git").exists():
            return ancestor

    if not ancestors:
        return start
    return ancestors[1] if len(ancestors) > 1 else ancestors[-1]
