"""Agreement 12334B92 — phase_05_inject CONTROL: error enumerate missing fields.

Pre-12334B92 the in-step missing-field path raised `ValueError` from
`_resolve_scratchpad` on first absent key (`scratchpad_dir`). The engine
turned that into an unstructured exception trace.

Post-12334B92 contract: `_write_injection_files` returns a structured
`StepResult(status="error", error_code="E_CTX_MISSING_FIELDS",
data={"missing_fields": [...sorted]})` listing ALL absent required keys
in one shot. Forward-compatible: as more required fields are added, all
get enumerated together rather than one-at-a-time fail-fast.

Engine-level `E_REQUIRED_CTX_MISSING` (org_config falsy) is unchanged —
that boundary fires before this in-step check.
"""
from __future__ import annotations

import pytest

import sys
from pathlib import Path

ENGINE_PY = Path(__file__).resolve().parent.parent
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from contracts import StepResult, WorkflowContext  # type: ignore
from phase_05_inject import (  # type: ignore
    _validate_org_config_required,
    _write_injection_files,
)


def _ctx(org_config: dict) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def test_validate_returns_empty_when_all_required_present():
    cfg = {"scratchpad_dir": "/tmp/scr"}
    assert _validate_org_config_required(cfg) == []


def test_validate_returns_missing_field_when_scratchpad_dir_absent():
    cfg = {"working_dir": "/tmp"}  # has unrelated key, but no scratchpad_dir
    assert _validate_org_config_required(cfg) == ["scratchpad_dir"]


def test_validate_treats_empty_string_scratchpad_dir_as_missing():
    cfg = {"scratchpad_dir": ""}
    assert _validate_org_config_required(cfg) == ["scratchpad_dir"]


def test_validate_returns_sorted_list():
    """When multiple required fields are added in future, must be sorted.

    Today there's only one required field, so this is a future-proofing
    assertion. We simulate by passing an empty cfg — single missing field
    sorts trivially as a 1-element list.
    """
    cfg = {}
    out = _validate_org_config_required(cfg)
    assert out == sorted(out)


def test_write_injection_files_returns_e_ctx_missing_fields_when_scratchpad_dir_absent():
    """_write_injection_files must return structured StepResult with
    error_code=E_CTX_MISSING_FIELDS and data["missing_fields"] listing keys.

    NOT a ValueError. NOT first-key-only. Enumerated, sorted, machine-readable.
    """
    ctx = _ctx({"working_dir": "/tmp"})  # no scratchpad_dir
    prev = StepResult(
        status="ok",
        data={"constitution_path": "/some/path"},
        duration_ms=0,
        step_name="discover_constitution",
    )
    result = _write_injection_files(ctx, prev)
    assert result.status == "error"
    assert result.error_code == "E_CTX_MISSING_FIELDS"
    assert isinstance(result.data, dict)
    assert "missing_fields" in result.data
    assert result.data["missing_fields"] == ["scratchpad_dir"]
    # Operator-readable error message must mention the field
    assert "scratchpad_dir" in (result.error or "")


def test_resolve_scratchpad_still_raises_value_error_as_safety_net():
    """Defense in depth: if a future caller bypasses the validator and
    invokes _resolve_scratchpad with missing field, ValueError still fires.
    Keeps existing test_resolve_scratchpad_raises... contract intact.
    """
    from phase_05_inject import _resolve_scratchpad  # type: ignore

    ctx = _ctx({"working_dir": "/tmp"})
    with pytest.raises(ValueError, match="scratchpad_dir"):
        _resolve_scratchpad(ctx)
