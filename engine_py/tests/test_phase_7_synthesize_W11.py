"""W11 RED tests — phase_7_synthesize NEEDS_CONTEXT silent-pass bug.

Closes /ultrareview Wave 11 finding #9 (HIGH):

* ``_write_synthesizer_artifact`` recognises STATUS_NEEDS_CONTEXT in the
  parser (``_parse_synthesizer_status``) but has NO explicit branch in the
  artifact-writer. Status falls through to ``status="ok"`` — synthesizer
  declaring "I lack context to synthesize" is silently treated as success.

Fix encoded in tests: add explicit ``if status == STATUS_NEEDS_CONTEXT``
branch returning ``StepResult(status="error",
error_code="E_SYNTHESIZER_NEEDS_CONTEXT")``. Sister-parity with the
existing BLOCKED branch (same ``common_data`` shape; ``recoverable=False``
mirrors BLOCKED — caller decides whether to expand context).

These tests target the engine end-to-end via ``WorkflowEngine.execute`` so
they exercise the same path the harness uses (matches sister tests in
``test_phase_7_synthesize.py``: ``test_synthesizer_status_blocked_returns_error``).

Expected:
  * ``test_W11_synthesizer_needs_context_returns_error`` — FAIL on current
    code (status=="ok"), PASS once the explicit branch lands.
  * ``test_W11_synthesizer_blocked_still_returns_blocked_regression`` —
    PASS on current code; sister regression guard.
  * ``test_W11_synthesizer_done_returns_ok_regression`` — PASS on current
    code; happy-path regression guard.

25e75663 migration: _make_ctx's llm_command shell-stub replaced with
register_backend pattern. Backend registered as "claude-subprocess" (default)
with overwrite=True; autouse fixture resets after each test (§1i singleton).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends  # noqa: E402
from bytedigger_engine import llm_subprocess as _llm_mod  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.workflows.phase_7_synthesize import (  # noqa: E402
    REPORT_DOC_RELPATH,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_NEEDS_CONTEXT,
    phase_7_synthesize_workflow,
)

# ---------------------------------------------------------------------------
# §1i autouse teardown: restore _BACKENDS singleton + clear telemetry context
# ---------------------------------------------------------------------------

_STUB_BACKEND_NAME = "claude-subprocess"  # overwrite default backend


@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    """§1i: restore _BACKENDS singleton + clear telemetry between tests."""
    monkeypatch.setattr(_llm_mod, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ---------------------------------------------------------------------------
# Stub backend
# ---------------------------------------------------------------------------


class _EchoBackend:
    """Returns a fixed payload as raw_response (merges extra_data for artifact writer)."""

    def __init__(self, payload: str):
        self._payload = payload

    def __call__(self, **kw) -> StepResult:
        data: dict = {
            "raw_response": self._payload,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        extra = kw.get("extra_data")
        if extra:
            data.update(extra)
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_synthesizer_llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


def _make_ctx(scratchpad: Path, llm_output: str) -> WorkflowContext:
    """Build context whose synthesizer backend emits llm_output verbatim."""
    register_backend(
        _STUB_BACKEND_NAME,
        _EchoBackend(llm_output),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    org = {"scratchpad_dir": str(scratchpad)}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="W11 needs-context test",
        session_id="test-session-W11",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── Finding #9: NEEDS_CONTEXT silent-pass ──────────────────────────────────


def test_W11_synthesizer_needs_context_returns_error(tmp_path):
    """STATUS: NEEDS_CONTEXT must surface as error, not silent ok.

    Reproduces #9. Current code lacks an explicit NEEDS_CONTEXT branch in
    ``_write_synthesizer_artifact`` — falls through to status="ok". Sister
    BLOCKED case correctly returns error. Fix: add explicit branch with
    error_code="E_SYNTHESIZER_NEEDS_CONTEXT", parity with BLOCKED.
    """
    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())

    result, _ = eng.execute(
        "p7",
        _make_ctx(
            scratchpad,
            "Need more context to synthesize report.\nSTATUS: NEEDS_CONTEXT\n",
        ),
    )

    assert result.status == "error", (
        f"Expected status='error' for NEEDS_CONTEXT (sister-parity with "
        f"BLOCKED branch). Got status={result.status!r}, "
        f"error_code={result.error_code!r}. Fix: add explicit "
        f"`if status == STATUS_NEEDS_CONTEXT` branch in "
        f"_write_synthesizer_artifact returning E_SYNTHESIZER_NEEDS_CONTEXT."
    )
    assert result.error_code == "E_SYNTHESIZER_NEEDS_CONTEXT", (
        f"Expected error_code='E_SYNTHESIZER_NEEDS_CONTEXT'. "
        f"Got {result.error_code!r}."
    )
    # common_data shape preserved (parity with BLOCKED branch)
    assert result.data is not None
    assert result.data["synthesizer_status"] == STATUS_NEEDS_CONTEXT
    # Report file MUST still be written (parity with BLOCKED — see line 333)
    assert (scratchpad / REPORT_DOC_RELPATH).exists()


# ─── Regression guards ──────────────────────────────────────────────────────


def test_W11_synthesizer_blocked_still_returns_blocked_regression(tmp_path):
    """Sister regression: existing BLOCKED handling preserved after fix.

    Guards against accidentally clobbering the BLOCKED branch when adding
    the NEEDS_CONTEXT branch (e.g. wrong order, fall-through).
    """
    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())

    result, _ = eng.execute(
        "p7",
        _make_ctx(
            scratchpad,
            "Cannot synthesize.\nSTATUS: BLOCKED\n",
        ),
    )

    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_BLOCKED"
    assert result.data["synthesizer_status"] == STATUS_BLOCKED
    assert result.recoverable is False
    assert (scratchpad / REPORT_DOC_RELPATH).exists()


def test_W11_synthesizer_done_returns_ok_regression(tmp_path):
    """Happy-path regression: STATUS: DONE still returns ok.

    Guards against the new branch accidentally catching DONE (wrong
    constant comparison, etc).
    """
    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())

    result, _ = eng.execute(
        "p7",
        _make_ctx(
            scratchpad,
            "Final Checkpoint reached.\nSTATUS: DONE\n",
        ),
    )

    assert result.status == "ok"
    assert result.data["synthesizer_status"] == STATUS_DONE
    assert (scratchpad / REPORT_DOC_RELPATH).exists()
