"""Environment for spawning the engine's own entry scripts by FILE PATH (bd#44).

Before 0.2.0 every such script bootstrapped itself with `sys.path.insert(0,
<engine_py>)` at import time. Those 63 inserts are exactly what a published
package must not carry (they are what let the flat top-level names keep
resolving), so bd#44 deleted them.

That leaves one gap, and only one: a script run as `python
<checkout>/engine_py/bytedigger_engine/run.py` gets `sys.path[0] =
.../bytedigger_engine`, and nothing on the path from which `import
bytedigger_engine` resolves. Installed users never hit it — site-packages is on
the default path, so the import works from any cwd — it is purely an artifact of
running out of a source checkout.

So the bootstrap moves OUT of the product and into the harness: the parent of
the package is announced through PYTHONPATH, which a child interpreter does
inherit. Deliberately NOT set process-wide in conftest: that would leak into the
clean venv the bd#44 acceptance test builds and make `import bytedigger_engine`
succeed there without an install — an acceptance oracle that passes over a
broken wheel. It is opt-in, per spawn.
"""
from __future__ import annotations

import os
from pathlib import Path

ENGINE_PY_ROOT = Path(__file__).resolve().parents[2]


def engine_env(base: dict[str, str] | None = None, **extra: str) -> dict[str, str]:
    """`base` (default: a copy of os.environ) with the package's parent
    prepended to PYTHONPATH, plus any `extra` overrides."""
    env = dict(os.environ if base is None else base)
    existing = env.get("PYTHONPATH", "")
    root = str(ENGINE_PY_ROOT)
    if root not in existing.split(os.pathsep):
        env["PYTHONPATH"] = root + (os.pathsep + existing if existing else "")
    env.update(extra)
    return env
