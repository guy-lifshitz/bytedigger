"""Single source of truth for the distribution name and its extras (GH1112).

Stdlib-only leaf by design (§1g + AC7): every install hint is rendered exactly
when the heavy dependency it advertises is *not* importable, so this module must
never import an engine_py module — no `lib.*`, no relative imports, nothing that
could itself fail. `llm_subprocess` and `doctor` import it at module scope, so it
is also listed in `pyproject.toml [tool.setuptools] py-modules` (AC38).

The extras below must stay in sync with `pyproject.toml`
`[project.optional-dependencies]`, and that synchronisation is ENFORCED, both
directions, by `tests/test_bd21_extras_parity.py` (bd#21). Adding an extra to
either file without the other reddens it.

`EXTRA_DBOS` used to be absent here, with a docstring claiming dbos was "a hard
dependency, not an extra, so a `bytedigger-engine[dbos]` hint would name an
extra pip cannot resolve". Both halves of that claim were false: `pyproject.toml`
declares `dbos` under `[project.optional-dependencies]` (so pip resolves the
extra fine), and the engine demonstrably runs without dbos — see the CI steps
`install wheel — no dbos` and `pytest — engine test suite (hermetic, no dbos,
no network)`, plus the two suites that skip themselves when dbos is missing
(`test_gh792_native_sentinel_emit.py`, `test_gh795_resolver_emit_seam.py`).
The claim survived because it was prose: it named AC9/AC10 as its enforcement
layer, and no such test existed. That is why the pin above is now a test.
"""
from __future__ import annotations

PACKAGE_DIST_NAME = "bytedigger-engine"

EXTRA_TEST = "test"
EXTRA_AGENTIC_PYDANTIC = "agentic-pydantic"
EXTRA_SECURITY = "security"
EXTRA_DBOS = "dbos"


def install_hint(extra: str | None = None) -> str:
    """Render the canonical pip command for this distribution.

    `install_hint("security")` -> 'python3 -m pip install "bytedigger-engine[security]"'
    `install_hint()`           -> 'python3 -m pip install "bytedigger-engine"'
    """
    target = f"{PACKAGE_DIST_NAME}[{extra}]" if extra else PACKAGE_DIST_NAME
    return f'python3 -m pip install "{target}"'
