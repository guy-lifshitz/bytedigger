#!/usr/bin/env python3
"""Executable shim for the bd#66 pre-commit enforcement layer.

The layer itself lives in `bytedigger_engine.precommit_enforce` and is a plain
library module: bd#44 forbids the installed package from touching `sys.path` or
importing a sibling by bare name, and this lot exists to stop a rule from being
declared without being enforced — weakening a neighbouring rule to ship it would
be the same defect in the opposite direction.

So the path bootstrap lives here instead, outside the package, next to
`install_git_hooks.py`. `githooks/pre-commit` execs this file; the exit code is
returned verbatim, so nothing sits between the layer's refusal and `git commit`.

Run from anywhere: the engine root is derived from this file's own location, never
from an inherited PYTHONPATH, which a bare developer clone does not have.
"""
from __future__ import annotations

import os
import sys

_ENGINE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "engine_py"
)
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

from bytedigger_engine.precommit_enforce import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
