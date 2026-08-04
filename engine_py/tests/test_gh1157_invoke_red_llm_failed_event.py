"""RED tests for GH1157 — `invoke_red_llm_failed` diagnostics event.

`_invoke_red_llm` (phase_5_implement.py) currently emits NO event on a
failure result from `invoke_llm_subprocess`; it falls straight through to
`return result`. GREEN adds a failure-branch emit of `invoke_red_llm_failed`
carrying error_code/exit_code/stderr_tail/duration_ms/model/cycle, then
returns the (unchanged) error result.

AC1  exactly ONE `invoke_red_llm_failed` event emitted on error result
AC2  payload stderr_tail verbatim from result.data
AC3  payload exit_code verbatim from result.data
AC4  payload carries error_code, model (non-None), cycle
AC5  returned StepResult unchanged passthrough (status/error_code preserved)
AC6  regression: ok-status result → ZERO invoke_red_llm_failed events
AC7  defensive: error result with data=None → still emits, exit_code/stderr_tail None, no exception

Import idiom: plain `import phase_5_implement as p5` (conftest-import-time
singleton adds engine_py root + workflows to sys.path — §1q / 81F97F3D gate),
matching tests/test_3F4B71D4_red_path_fallback_chain.py. UUT (`_invoke_red_llm`)
is called directly and NOT mocked (§1l); only `invoke_llm_subprocess` and
`_emit_safe` are stubbed.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from bytedigger_engine.workflows import phase_5_implement as p5
from bytedigger_engine.contracts import StepResult, WorkflowContext


def _make_scratchpad() -> Path:
    """Return a real-path'd tmpdir (§1j)."""
    return Path(os.path.realpath(tempfile.mkdtemp()))


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="GH1157 invoke_red_llm_failed test",
        session_id="test-gh1157",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(scratchpad: Path, cycle: int = 3) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "p",
            "log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
            "stable_prefix": "",
        },
        duration_ms=0,
        step_name="prior_step",
    )


def _patch_emit_p5(monkeypatch) -> list[dict]:
    """Monkeypatch phase_5_implement._emit_safe and capture all calls.

    Matches the sibling pattern in test_3F4B71D4_red_path_fallback_chain.py.
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        p5,
        "_emit_safe",
        lambda et, payload, **kw: captured.append({"type": et, "payload": payload}),
    )
    return captured


def _patch_error_llm_subprocess(monkeypatch, data):
    """Stub the dependency (invoke_llm_subprocess), NOT the UUT (§1l)."""
    fake_result = StepResult(
        status="error",
        data=data,
        duration_ms=42,
        step_name="invoke_red_llm",
        error="boom happened",
        error_code="E_LLM_TIMEOUT",
    )
    monkeypatch.setattr(p5, "invoke_llm_subprocess", lambda **kw: fake_result)
    return fake_result


def _patch_ok_llm_subprocess(monkeypatch, data):
    fake_result = StepResult(
        status="ok",
        data=data,
        duration_ms=10,
        step_name="invoke_red_llm",
    )
    monkeypatch.setattr(p5, "invoke_llm_subprocess", lambda **kw: fake_result)
    return fake_result


def _stub_ok_path_helpers(monkeypatch):
    """No-op the ok-path-only helpers so the ok regression test doesn't crash
    on unrelated dependencies (scratchpad/worktree/cross-tree warning)."""
    monkeypatch.setattr(p5, "_resolve_scratchpad", lambda ctx: "/tmp/fake-scratchpad")
    monkeypatch.setattr(p5, "_resolve_worktree_root", lambda ctx, sp: "/tmp/fake-worktree")
    monkeypatch.setattr(p5, "_maybe_emit_cross_tree_warning", lambda result, root: result)


def test_ac1_exactly_one_failed_event_emitted(monkeypatch):
    sp = _make_scratchpad()
    captured = _patch_emit_p5(monkeypatch)
    _patch_error_llm_subprocess(
        monkeypatch, {"stderr_tail": "boom", "exit_code": 1, "duration_ms": 42}
    )
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    p5._invoke_red_llm(ctx, prev)

    matches = [e for e in captured if e["type"] == "invoke_red_llm_failed"]
    assert len(matches) == 1, (
        f"Expected exactly ONE invoke_red_llm_failed event, got {len(matches)}: "
        f"{[e['type'] for e in captured]!r} (AC1). Pre-GREEN: no such event exists — FAIL expected."
    )


def test_ac2_payload_stderr_tail_verbatim(monkeypatch):
    sp = _make_scratchpad()
    captured = _patch_emit_p5(monkeypatch)
    _patch_error_llm_subprocess(
        monkeypatch, {"stderr_tail": "boom", "exit_code": 1, "duration_ms": 42}
    )
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    p5._invoke_red_llm(ctx, prev)

    matches = [e for e in captured if e["type"] == "invoke_red_llm_failed"]
    assert matches, "No invoke_red_llm_failed event captured (AC2) — pre-GREEN FAIL expected."
    assert matches[0]["payload"].get("stderr_tail") == "boom", (
        f"Expected payload stderr_tail=='boom', got {matches[0]['payload'].get('stderr_tail')!r} (AC2)."
    )


def test_ac3_payload_exit_code_verbatim(monkeypatch):
    sp = _make_scratchpad()
    captured = _patch_emit_p5(monkeypatch)
    _patch_error_llm_subprocess(
        monkeypatch, {"stderr_tail": "boom", "exit_code": 1, "duration_ms": 42}
    )
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    p5._invoke_red_llm(ctx, prev)

    matches = [e for e in captured if e["type"] == "invoke_red_llm_failed"]
    assert matches, "No invoke_red_llm_failed event captured (AC3) — pre-GREEN FAIL expected."
    assert matches[0]["payload"].get("exit_code") == 1, (
        f"Expected payload exit_code==1, got {matches[0]['payload'].get('exit_code')!r} (AC3)."
    )


def test_ac4_payload_carries_error_code_model_cycle(monkeypatch):
    sp = _make_scratchpad()
    captured = _patch_emit_p5(monkeypatch)
    _patch_error_llm_subprocess(
        monkeypatch, {"stderr_tail": "boom", "exit_code": 1, "duration_ms": 42}
    )
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    p5._invoke_red_llm(ctx, prev)

    matches = [e for e in captured if e["type"] == "invoke_red_llm_failed"]
    assert matches, "No invoke_red_llm_failed event captured (AC4) — pre-GREEN FAIL expected."
    payload = matches[0]["payload"]
    assert payload.get("error_code") == "E_LLM_TIMEOUT", (
        f"Expected error_code=='E_LLM_TIMEOUT', got {payload.get('error_code')!r} (AC4)."
    )
    assert "model" in payload and payload["model"] is not None, (
        f"Expected non-None 'model' key in payload, got {payload!r} (AC4)."
    )
    assert payload.get("cycle") == 3, (
        f"Expected cycle==3, got {payload.get('cycle')!r} (AC4)."
    )


def test_ac5_returned_result_unchanged_passthrough(monkeypatch):
    sp = _make_scratchpad()
    _patch_emit_p5(monkeypatch)
    fake_result = _patch_error_llm_subprocess(
        monkeypatch, {"stderr_tail": "boom", "exit_code": 1, "duration_ms": 42}
    )
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    result = p5._invoke_red_llm(ctx, prev)

    assert result.status == "error", (
        f"Expected passthrough status=='error', got {result.status!r} (AC5)."
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        f"Expected passthrough error_code=='E_LLM_TIMEOUT', got {result.error_code!r} (AC5)."
    )
    assert result is fake_result, (
        "Expected the SAME error StepResult object returned unchanged (AC5)."
    )


def test_ac6_ok_status_emits_zero_failed_events(monkeypatch):
    sp = _make_scratchpad()
    captured = _patch_emit_p5(monkeypatch)
    _stub_ok_path_helpers(monkeypatch)
    _patch_ok_llm_subprocess(
        monkeypatch,
        {
            "log_path": str(sp / "tests/build-red-output.log"),
            "spec_path": str(sp / "specs/build-spec.md"),
            "cycle": 3,
        },
    )
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    result = p5._invoke_red_llm(ctx, prev)

    assert result.status == "ok"
    matches = [e for e in captured if e["type"] == "invoke_red_llm_failed"]
    assert matches == [], (
        f"Expected ZERO invoke_red_llm_failed events on ok-status result, got {matches!r} (AC6)."
    )


def test_ac7_error_with_none_data_still_emits_defensively(monkeypatch):
    sp = _make_scratchpad()
    captured = _patch_emit_p5(monkeypatch)
    _patch_error_llm_subprocess(monkeypatch, None)
    ctx = _make_ctx(sp)
    prev = _make_prev(sp, cycle=3)

    result = p5._invoke_red_llm(ctx, prev)

    matches = [e for e in captured if e["type"] == "invoke_red_llm_failed"]
    assert len(matches) == 1, (
        f"Expected exactly ONE invoke_red_llm_failed event even with data=None, "
        f"got {len(matches)}: {[e['type'] for e in captured]!r} (AC7)."
    )
    payload = matches[0]["payload"]
    assert payload.get("stderr_tail") is None, (
        f"Expected stderr_tail None when result.data is None, got {payload.get('stderr_tail')!r} (AC7)."
    )
    assert payload.get("exit_code") is None, (
        f"Expected exit_code None when result.data is None, got {payload.get('exit_code')!r} (AC7)."
    )
    assert result.status == "error"
