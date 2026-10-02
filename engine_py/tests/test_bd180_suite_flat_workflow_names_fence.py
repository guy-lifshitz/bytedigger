"""bd#180 AC1: the test suite must not leak bytedigger_engine/workflows onto sys.path.

Since bd#44 the phase modules live at bytedigger_engine.workflows.<name>, and
conftest deliberately keeps that directory off sys.path. A test module that
inserts it (at collection time) makes flat names such as `phase_7_synthesize`
importable and hides tests that only work because of that leak.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_WORKFLOWS_DIR = (
    Path(__file__).resolve().parent.parent / "bytedigger_engine" / "workflows"
).resolve()

_FLAT_NAMES = [
    "phase_7_synthesize",
    "phase_1_discovery",
    "phase_05_inject",
    "phase_5_implement",
]


def test_workflows_dir_not_on_sys_path() -> None:
    offenders = []
    for entry in sys.path:
        try:
            if Path(entry or ".").resolve() == _WORKFLOWS_DIR:
                offenders.append(entry)
        except (OSError, RuntimeError, ValueError):
            continue
    assert not offenders, (
        f"bd#44 fence breached: sys.path entries resolve to {_WORKFLOWS_DIR}: "
        f"{offenders!r}"
    )


@pytest.mark.parametrize("name", _FLAT_NAMES)
def test_flat_workflow_name_not_importable(name: str) -> None:
    spec = importlib.util.find_spec(name)
    assert spec is None, (
        f"flat name {name!r} resolved to {spec.origin!r} -- bd#44 fence "
        f"breached by a test-suite sys.path leak"
    )
