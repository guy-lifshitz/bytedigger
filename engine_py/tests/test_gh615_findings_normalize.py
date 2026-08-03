"""RED tests for GH615: engine.py normalize `findings` to str at retry boundary.

engine.py:406 does `findings = result.data.get("findings", "")` with no type
normalization. When a retry StepResult carries findings as a list-of-dicts
(green_lint PASS-with-warnings shape), the retry branch crashes with
AttributeError on `.encode()` at :429/:440.

AC1-AC5: list-of-dicts findings must be normalized to a human-readable str at
the retry boundary (via a new `_findings_to_str` helper) so no exception is
raised and downstream consumers (events, next step's prev) see str.
AC6: str findings path is unaffected (regression guard) — passes today.
AC7: `_findings_to_str` helper direct-call behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine import engine as engine_module  # noqa: E402


# ─── helpers (mirrors tests/test_engine_retry_data_forwarding.py) ─────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


class _FakeEventLog:
    """Minimal duck-typed event log that records (event_type, payload, run_id) tuples."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str) -> None:
        self.events.append((event_type, payload, run_id))

    def of_type(self, event_type: str) -> list[dict]:
        return [p for et, p, _ in self.events if et == event_type]


def _build_two_step_workflow(
    step0_retry_data: dict,
    captured_prev: list[Any],
) -> WorkflowDefinition:
    """Build a minimal 2-step workflow (same shape as the retry-forwarding harness)."""
    call_counts = {"step0": 0}

    def step0_execute(ctx, prev):
        call_counts["step0"] += 1
        if call_counts["step0"] == 1:
            return StepResult(
                status="error",
                data=step0_retry_data,
                duration_ms=0,
                step_name="step0",
                error_code="E_GREEN_LINT_RETRY",
                recoverable=True,
            )
        return StepResult(status="ok", data={"step0_second": True}, duration_ms=0, step_name="step0")

    def step1_execute(ctx, prev):
        captured_prev.append(prev)
        return StepResult(status="ok", data={"probe": True}, duration_ms=0, step_name="step1")

    return WorkflowDefinition(
        name="test_gh615",
        steps=[
            StepContract(name="step0", execute=step0_execute),
            StepContract(name="step1_probe", execute=step1_execute),
        ],
    )


_LIST_FINDINGS = [{"check_id": "gl_rule", "path": "a.py", "start": 3}]
_EXPECTED_STR = "gl_rule at a.py:3"


def _run_retry(tmp_path: Path, findings: Any, extra: dict | None = None):
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 1,
        "findings": findings,
        **(extra or {}),
    }
    workflow = _build_two_step_workflow(retry_data, captured)
    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_gh615", workflow)
    ctx = _make_ctx(tmp_path)
    result, _ = engine.execute("test_gh615", ctx)
    return result, event_log, captured


# ─── AC1: retry branch completes without exception for list findings ─────────


def test_ac1_retry_with_list_findings_completes_without_exception(tmp_path):
    """AC1: list-of-dicts findings must not crash the retry branch; both
    iteration_started and phase_retry_triggered events present.

    FAILS TODAY: engine.py:429 `.encode()` on a list raises AttributeError.
    """
    result, event_log, _ = _run_retry(tmp_path, _LIST_FINDINGS)
    assert result.status == "ok", (
        f"expected ok after retry, got {result.status!r} error={result.error!r}"
    )
    assert len(event_log.of_type("iteration_started")) == 1
    assert len(event_log.of_type("phase_retry_triggered")) == 1


# ─── AC2: iteration_started.findings_summary is normalized str ───────────────


def test_ac2_findings_summary_is_normalized_str(tmp_path):
    """AC2: findings_summary is str, equals expected normalized value, len<=500.

    FAILS TODAY: crash before this event's assert path can even be reached
    with correct str semantics (list slicing produces a list, not a str).
    """
    _, event_log, _ = _run_retry(tmp_path, _LIST_FINDINGS)
    evts = event_log.of_type("iteration_started")
    assert len(evts) == 1
    summary = evts[0]["findings_summary"]
    assert isinstance(summary, str), f"expected str, got {type(summary)}"
    assert summary == _EXPECTED_STR[:500]
    assert len(summary) <= 500


# ─── AC3: phase_retry_triggered.findings_bytes is correct int ────────────────


def test_ac3_findings_bytes_is_int_utf8_length(tmp_path):
    """AC3: findings_bytes == len(normalized_str.encode('utf-8')), an int.

    FAILS TODAY: AttributeError before this event is emitted.
    """
    _, event_log, _ = _run_retry(tmp_path, _LIST_FINDINGS)
    evts = event_log.of_type("phase_retry_triggered")
    assert len(evts) == 1
    fb = evts[0]["findings_bytes"]
    assert isinstance(fb, int)
    assert fb == len(_EXPECTED_STR.encode("utf-8"))


# ─── AC4: findings_truncated event with list findings, no exception ──────────


def test_ac4_findings_truncated_event_with_list_findings(tmp_path):
    """AC4: findings_truncated=True + findings_original_bytes=999 with list
    findings emits findings_truncated event, truncated_to is int, no exception.

    FAILS TODAY: AttributeError before this branch is reached.
    """
    _, event_log, _ = _run_retry(
        tmp_path,
        _LIST_FINDINGS,
        extra={"findings_truncated": True, "findings_original_bytes": 999},
    )
    evts = event_log.of_type("findings_truncated")
    assert len(evts) == 1
    truncated_to = evts[0]["truncated_to"]
    assert isinstance(truncated_to, int)


# ─── AC5: next step's prev['findings'] is normalized str ─────────────────────


def test_ac5_next_step_prev_findings_is_str(tmp_path):
    """AC5: step1 probe's captured prev['findings'] (retry pass) is str,
    equals the normalized value.

    FAILS TODAY: prev['findings'] is the raw list (or crash occurs first).
    """
    result, _, captured = _run_retry(tmp_path, _LIST_FINDINGS)
    assert result.status == "ok"
    assert len(captured) >= 1
    retry_prev = captured[0]
    assert isinstance(retry_prev, dict)
    assert isinstance(retry_prev.get("findings"), str), (
        f"expected str findings in prev, got {type(retry_prev.get('findings'))}"
    )
    assert retry_prev["findings"] == _EXPECTED_STR


# ─── AC6: str findings path unchanged (regression — passes today) ────────────


def test_ac6_str_findings_path_unchanged_regression(tmp_path):
    """AC6: plain str findings behave exactly as before (regression guard).

    PASSES TODAY: existing code already handles str findings correctly.
    """
    plain = "plain string finding"
    result, event_log, captured = _run_retry(tmp_path, plain)
    assert result.status == "ok"
    summary_evts = event_log.of_type("iteration_started")
    assert summary_evts[0]["findings_summary"] == plain
    retry_prev = captured[0]
    assert retry_prev["findings"] == plain
    bytes_evts = event_log.of_type("phase_retry_triggered")
    assert bytes_evts[0]["findings_bytes"] == len(plain.encode("utf-8"))


# ─── AC7: _findings_to_str helper direct-call behavior ───────────────────────


def test_ac7_findings_to_str_helper_exists_and_normalizes():
    """AC7: `_findings_to_str` exists on engine module and normalizes all
    the enumerated legacy-input cases (§2.3).

    FAILS TODAY: `_findings_to_str` does not exist on engine.py yet.
    """
    helper = getattr(engine_module, "_findings_to_str", None)
    assert helper is not None, "engine._findings_to_str does not exist yet"

    d1 = {"check_id": "X", "path": "a.py", "start": 3}
    d2 = {"check_id": "Y", "path": "b.py", "start": 7}
    assert helper([d1, d2]) == "X at a.py:3; Y at b.py:7"
    assert helper("s") == "s"
    assert helper(None) == ""
    assert helper([]) == ""

    # Pathological input must not raise.
    result = helper([object()])
    assert isinstance(result, str)
