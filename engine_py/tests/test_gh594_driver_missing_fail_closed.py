"""GH594 RED — phase_45_spec driver-missing paths become fail-closed errors.

Spec: SHARED/memory/Decisions/2026-07-11_GH594_bootstrap_completeness_spec.md
§2.3: `_verify_spec_cite_lint` / `verify_spec_lint` currently return
status="ok" + `*_skipped="driver_missing"` when the lint driver file is
absent (silent pass). Post-GREEN they must return status="error" with new
error codes E_SPEC_CITE_LINT_UNAVAILABLE / E_SPEC_LINT_UNAVAILABLE, while
still emitting `gate_disabled` telemetry (unchanged).

No module-level sys.path manipulation here (§1q / 81F97F3D) — conftest.py's
conftest-import-time singleton already puts engine_py root + workflows/ on
sys.path. All symbols under test (_verify_spec_cite_lint, _verify_spec_lint)
already exist in production today — only their driver-missing RETURN VALUE
changes, so this file collects cleanly.
"""
from __future__ import annotations

from types import SimpleNamespace

import phase_45_spec as p45  # type: ignore
import telemetry_ctx  # type: ignore
from contracts import StepResult  # type: ignore


class _CaptureEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type, payload, run_id):
        self.events.append((event_type, payload, run_id))


class _StubRunCtx:
    def __init__(self, event_log):
        self.event_log = event_log
        self.run_id = "gh594-test-run"


def _gate_disabled_payloads(log: _CaptureEventLog) -> list[dict]:
    return [payload for (etype, payload, _rid) in log.events if etype == "gate_disabled"]


def _patch_telemetry(monkeypatch, log: _CaptureEventLog):
    monkeypatch.setattr(telemetry_ctx, "get_current_run", lambda: _StubRunCtx(log))


# ─── AC5: _verify_spec_cite_lint driver-missing → error + E_SPEC_CITE_LINT_UNAVAILABLE ───


def test_ac5_verify_spec_cite_lint_driver_missing_returns_error_and_emits_gate_disabled(
    monkeypatch,
):
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)
    monkeypatch.setattr(p45.Path, "is_file", lambda self: False)

    ctx = SimpleNamespace(org_config={})
    prev = StepResult(
        status="ok", data={"spec_path": "/tmp/fake-spec.md"}, duration_ms=0, step_name="prev"
    )

    result = p45._verify_spec_cite_lint(ctx, prev)

    # Fails RED today: prod currently returns status="ok" / skipped="driver_missing".
    assert result.status == "error"
    assert result.error_code == "E_SPEC_CITE_LINT_UNAVAILABLE"

    payloads = _gate_disabled_payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["gate"] == "spec_cite_lint"
    assert payloads[0]["reason"] == "driver_missing"


# ─── AC6: verify_spec_lint driver-missing → error + E_SPEC_LINT_UNAVAILABLE ───


def test_ac6_verify_spec_lint_driver_missing_returns_error_and_emits_gate_disabled(monkeypatch):
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)
    monkeypatch.setattr(p45.Path, "is_file", lambda self: False)

    ctx = SimpleNamespace(org_config={})
    prev = StepResult(
        status="ok", data={"spec_path": "/tmp/fake-spec.md"}, duration_ms=0, step_name="prev"
    )

    result = p45._verify_spec_lint(ctx, prev)

    # Fails RED today: prod currently returns status="ok" / skipped="driver_missing".
    assert result.status == "error"
    assert result.error_code == "E_SPEC_LINT_UNAVAILABLE"

    payloads = _gate_disabled_payloads(log)
    assert len(payloads) == 1
    assert payloads[0]["gate"] == "spec_lint"
    assert payloads[0]["reason"] == "driver_missing"


# ─── AC7: org_config skip flags are unaffected — regression pin ───


def test_ac7_spec_cite_lint_config_skip_still_returns_ok(monkeypatch):
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    ctx = SimpleNamespace(org_config={"spec_cite_lint_skip": True})
    prev = StepResult(
        status="ok", data={"spec_path": "/tmp/fake-spec.md"}, duration_ms=0, step_name="prev"
    )

    result = p45._verify_spec_cite_lint(ctx, prev)

    assert result.status == "ok"
    assert result.data["spec_cite_lint_skipped"] == "config_skip"


def test_ac7_spec_lint_config_skip_still_returns_ok(monkeypatch):
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    ctx = SimpleNamespace(org_config={"spec_lint_skip": True})
    prev = StepResult(
        status="ok", data={"spec_path": "/tmp/fake-spec.md"}, duration_ms=0, step_name="prev"
    )

    result = p45._verify_spec_lint(ctx, prev)

    assert result.status == "ok"
    assert result.data["spec_lint_skipped"] == "config_skip"
