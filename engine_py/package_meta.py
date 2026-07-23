"""Single source of truth for the distribution name and its extras (GH1112).

Stdlib-only leaf by design (§1g + AC7): every install hint is rendered exactly
when the heavy dependency it advertises is *not* importable, so this module must
never import an engine_py module — no `lib.*`, no relative imports, nothing that
could itself fail. `llm_subprocess` and `doctor` import it at module scope, so it
is also listed in `pyproject.toml [tool.setuptools] py-modules` (AC38).

The extras below must stay in sync with `pyproject.toml`
`[project.optional-dependencies]` — AC9/AC10 pin both directions. There is
deliberately NO `EXTRA_DBOS`: dbos is a hard dependency, not an extra, so a
`bytedigger-engine[dbos]` hint would name an extra pip cannot resolve.
"""
from __future__ import annotations

PACKAGE_DIST_NAME = "bytedigger-engine"

EXTRA_TEST = "test"
EXTRA_AGENTIC_PYDANTIC = "agentic-pydantic"
EXTRA_SECURITY = "security"


def install_hint(extra: str | None = None) -> str:
    """Render the canonical pip command for this distribution.

    `install_hint("security")` -> 'pip install "bytedigger-engine[security]"'
    `install_hint()`           -> 'pip install "bytedigger-engine"'
    """
    target = f"{PACKAGE_DIST_NAME}[{extra}]" if extra else PACKAGE_DIST_NAME
    return f'pip install "{target}"'
