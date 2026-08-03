"""Agreement 1E8EF652 — _emit_safe ALERT events log at error severity on
event_log fallback.

Pre-1E8EF652 the fallback path always called `logger.warning(...)`, even for
ALERT-class events (`green_token_budget_alert`, `green_watchdog_tokens_unknown`)
where a degraded GREEN run is happening AND telemetry is broken — silent
warnings hide compounding signal.

Post-1E8EF652 contract:
  - `_emit_safe` accepts an optional `severity: str = "warning"` kwarg.
  - severity="error" routes the fallback log to `logger.error(...)`.
  - severity="warning" (default) preserves existing behavior.
  - At least one ALERT-class call site (`green_token_budget_alert`) passes
    severity="error" so the fallback fires logger.error when event_log raises.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ENGINE_PY = Path(__file__).resolve().parent.parent
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.workflows import phase_5_implement as p5  # type: ignore


class _BoomEventLog:
    def append(self, *args, **kwargs):
        raise RuntimeError("event_log boom")


class _StubRunCtx:
    def __init__(self):
        self.event_log = _BoomEventLog()
        self.run_id = "test-run"


def test_emit_safe_default_severity_routes_to_logger_warning(monkeypatch, caplog):
    monkeypatch.setattr(p5.telemetry_ctx, "get_current_run", lambda: _StubRunCtx())
    with caplog.at_level(logging.DEBUG, logger=p5.logger.name):
        p5._emit_safe("some_event", {"k": 1})
    levels = {r.levelno for r in caplog.records if "telemetry append failed" in r.getMessage()}
    assert logging.WARNING in levels
    assert logging.ERROR not in levels


def test_emit_safe_severity_error_routes_to_logger_error(monkeypatch, caplog):
    monkeypatch.setattr(p5.telemetry_ctx, "get_current_run", lambda: _StubRunCtx())
    with caplog.at_level(logging.DEBUG, logger=p5.logger.name):
        p5._emit_safe("some_alert", {"k": 1}, severity="error")
    levels = {r.levelno for r in caplog.records if "telemetry append failed" in r.getMessage()}
    assert logging.ERROR in levels
    assert logging.WARNING not in levels


def test_emit_safe_unknown_severity_falls_back_to_warning(monkeypatch, caplog):
    """Defensive: an unrecognized severity string must not crash. Default to warning."""
    monkeypatch.setattr(p5.telemetry_ctx, "get_current_run", lambda: _StubRunCtx())
    with caplog.at_level(logging.DEBUG, logger=p5.logger.name):
        p5._emit_safe("evt", {}, severity="bogus")
    levels = {r.levelno for r in caplog.records if "telemetry append failed" in r.getMessage()}
    assert logging.WARNING in levels


def test_check_green_token_budget_uses_severity_error_for_alert(monkeypatch, caplog):
    """ALERT-class call site `green_token_budget_alert` must propagate severity="error"
    so the fallback log fires `logger.error` (not warning).
    """
    monkeypatch.setattr(p5.telemetry_ctx, "get_current_run", lambda: _StubRunCtx())
    from bytedigger_engine.contracts import StepResult  # type: ignore

    prev = StepResult(
        status="ok",
        data={"tokens_out": p5.GREEN_OUTPUT_TOKEN_BUDGET + 1},
        duration_ms=0,
        step_name="invoke_green_llm",
    )

    class _Ctx:
        org_config = {}
        session_id = "s"

    with caplog.at_level(logging.DEBUG, logger=p5.logger.name):
        result = p5._check_green_token_budget(_Ctx(), prev)
    assert result.status == "ok"  # alert, not failure
    err_records = [r for r in caplog.records if "telemetry append failed" in r.getMessage()]
    assert len(err_records) >= 1
    assert any(r.levelno == logging.ERROR for r in err_records)
