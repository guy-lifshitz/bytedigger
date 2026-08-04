"""RED tests for 2FDA949D — model-pin warning for non-hard-gate in-session steps.

Spec: SHARED/memory/Decisions/2026-06-18_2FDA949D_model_pin_warn_spec.md

ACs covered:
  AC1  — _model_family("opus") == "opus"
  AC2  — _model_family("claude-sonnet-4-6") == "sonnet"  (alias-aware)
  AC3  — _model_family("claude-haiku-4-5-20251001") == "haiku"
  AC4  — _model_family("octopus-3") is None AND _model_family(None) is None
  AC5  — drift detected: dispatched haiku vs pinned sonnet → dict returned
  AC6  — no drift when families match (dispatched claude-sonnet-4-6 vs pinned sonnet)
  AC7  — hard_gate=True → returns None (deferred to hard-gate path)
  AC8  — no dispatched_model → returns None
  AC9  — no pinned_model → returns None
  AC10 — end-to-end: non-hard-gate step with model drift emits "model_pin_mismatch"
          event, StepResult.status == "ok" (non-blocking warn)
  AC11 — regression: _assert_in_session_model_or_downgrade still blocks hard-gate
          downgrade with E_HARD_GATE_MODEL_DOWNGRADE + recoverable=False

§1q (D1CF5FDF): `_model_family` and `_detect_nonhardgate_model_drift` do NOT exist
  yet — imported INSIDE each test body (deferred), so the file collects without
  error pre-GREEN.  `_invoke_in_session`, `_is_opus_model`, and
  `_assert_in_session_model_or_downgrade` already exist — top-level imports are safe.

§1l: every AC anchors on real returns from real helpers using on-disk or dict
  fixtures.  No mocking/patching of the UUT symbols.

§1i (ABD52613 / workflows.md §1i): AC10 uses a daemon thread that polls for the
  .req.json to appear AFTER the engine's pre-wait stale sweep
  (llm_subprocess.py:549-553), then atomically writes the .res.json — identical
  to the sibling test_02FF48F4_model_pin_insession.py AC2 pattern.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

# Grandfathered sys.path pattern — matches all sibling tests in this directory.
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

# Top-level imports of already-existing symbols only.
from bytedigger_engine.llm_subprocess import _invoke_in_session, _is_opus_model, _assert_in_session_model_or_downgrade  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, dict(payload), run_id or "ad-hoc"))

    def by_type(self, event_type: str) -> list[dict]:
        return [payload for (et, payload, _) in self.events if et == event_type]


@pytest.fixture(autouse=True)
def _clear_ctx():
    """Ensure no stale RunContext leaks between tests."""
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ---------------------------------------------------------------------------
# AC1 — _model_family("opus") == "opus"
# ---------------------------------------------------------------------------

def test_ac1_model_family_opus() -> None:
    """AC1: _model_family('opus') must return 'opus'.

    Pre-GREEN FAIL: ImportError — _model_family does not exist yet.
    """
    # Deferred import per §1q (D1CF5FDF): symbol does not exist yet.
    from bytedigger_engine.llm_subprocess import _model_family  # type: ignore[attr-defined]

    result = _model_family("opus")
    assert result == "opus", (
        f"AC1: _model_family('opus') must return 'opus'; got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — _model_family("claude-sonnet-4-6") == "sonnet" (alias-aware)
# ---------------------------------------------------------------------------

def test_ac2_model_family_sonnet_alias() -> None:
    """AC2: _model_family('claude-sonnet-4-6') must return 'sonnet' (alias-aware).

    Pre-GREEN FAIL: ImportError — _model_family does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _model_family  # type: ignore[attr-defined]

    result = _model_family("claude-sonnet-4-6")
    assert result == "sonnet", (
        f"AC2: _model_family('claude-sonnet-4-6') must return 'sonnet'; got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — _model_family("claude-haiku-4-5-20251001") == "haiku"
# ---------------------------------------------------------------------------

def test_ac3_model_family_haiku_full_id() -> None:
    """AC3: _model_family('claude-haiku-4-5-20251001') must return 'haiku'.

    Pre-GREEN FAIL: ImportError — _model_family does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _model_family  # type: ignore[attr-defined]

    result = _model_family("claude-haiku-4-5-20251001")
    assert result == "haiku", (
        f"AC3: _model_family('claude-haiku-4-5-20251001') must return 'haiku'; "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — _model_family("octopus-3") is None AND _model_family(None) is None
# ---------------------------------------------------------------------------

def test_ac4_model_family_unknown_and_none() -> None:
    """AC4: _model_family('octopus-3') must return None (anchored — no false-positive)
    AND _model_family(None) must return None.

    Pre-GREEN FAIL: ImportError — _model_family does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _model_family  # type: ignore[attr-defined]

    result_unknown = _model_family("octopus-3")
    assert result_unknown is None, (
        f"AC4: _model_family('octopus-3') must return None (no false-positive); "
        f"got {result_unknown!r}"
    )

    result_none = _model_family(None)
    assert result_none is None, (
        f"AC4: _model_family(None) must return None; got {result_none!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — drift detected: dispatched haiku vs pinned sonnet → dict returned
# ---------------------------------------------------------------------------

def test_ac5_detect_drift_haiku_vs_sonnet() -> None:
    """AC5: _detect_nonhardgate_model_drift with dispatched 'haiku' and pinned 'sonnet'
    must return a dict with observed_model=='haiku', pinned_model=='sonnet',
    step_name=='invoke_green_llm'.

    Pre-GREEN FAIL: ImportError — _detect_nonhardgate_model_drift does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _detect_nonhardgate_model_drift  # type: ignore[attr-defined]

    result = _detect_nonhardgate_model_drift(
        {"dispatched_model": "haiku"},
        "sonnet",
        False,
        "invoke_green_llm",
    )

    assert result is not None, (
        "AC5: _detect_nonhardgate_model_drift must return a dict when families differ "
        "(haiku vs sonnet); got None"
    )
    assert result.get("observed_model") == "haiku", (
        f"AC5: result['observed_model'] must be 'haiku'; got {result.get('observed_model')!r}"
    )
    assert result.get("pinned_model") == "sonnet", (
        f"AC5: result['pinned_model'] must be 'sonnet'; got {result.get('pinned_model')!r}"
    )
    assert result.get("step_name") == "invoke_green_llm", (
        f"AC5: result['step_name'] must be 'invoke_green_llm'; "
        f"got {result.get('step_name')!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — no drift when families match (claude-sonnet-4-6 dispatched vs sonnet pinned)
# ---------------------------------------------------------------------------

def test_ac6_no_drift_on_family_match() -> None:
    """AC6: _detect_nonhardgate_model_drift must return None when dispatched model
    'claude-sonnet-4-6' and pinned 'sonnet' resolve to the same family — no false drift.

    Pre-GREEN FAIL: ImportError — _detect_nonhardgate_model_drift does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _detect_nonhardgate_model_drift  # type: ignore[attr-defined]

    result = _detect_nonhardgate_model_drift(
        {"dispatched_model": "claude-sonnet-4-6"},
        "sonnet",
        False,
        "invoke_red_llm",
    )

    assert result is None, (
        f"AC6: _detect_nonhardgate_model_drift must return None when dispatched "
        f"'claude-sonnet-4-6' matches pinned family 'sonnet'; got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — hard_gate=True → returns None (deferred to hard-gate path)
# ---------------------------------------------------------------------------

def test_ac7_hard_gate_true_returns_none() -> None:
    """AC7: _detect_nonhardgate_model_drift must return None when hard_gate=True
    regardless of model drift (hard-gate path owns the check, not this helper).

    Pre-GREEN FAIL: ImportError — _detect_nonhardgate_model_drift does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _detect_nonhardgate_model_drift  # type: ignore[attr-defined]

    result = _detect_nonhardgate_model_drift(
        {"dispatched_model": "haiku"},
        "sonnet",
        True,
        "validation",
    )

    assert result is None, (
        f"AC7: _detect_nonhardgate_model_drift must return None when hard_gate=True "
        f"(deferred to hard-gate path); got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — no dispatched_model → returns None
# ---------------------------------------------------------------------------

def test_ac8_no_dispatched_model_returns_none() -> None:
    """AC8: _detect_nonhardgate_model_drift must return None when result_obj has
    no dispatched_model key (cannot determine observed family).

    Pre-GREEN FAIL: ImportError — _detect_nonhardgate_model_drift does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _detect_nonhardgate_model_drift  # type: ignore[attr-defined]

    result = _detect_nonhardgate_model_drift(
        {},
        "sonnet",
        False,
        "invoke_green_llm",
    )

    assert result is None, (
        f"AC8: _detect_nonhardgate_model_drift must return None when dispatched_model "
        f"is absent from result_obj; got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC9 — no pinned_model → returns None
# ---------------------------------------------------------------------------

def test_ac9_no_pinned_model_returns_none() -> None:
    """AC9: _detect_nonhardgate_model_drift must return None when pinned_model is None
    (no pin configured — nothing to compare against).

    Pre-GREEN FAIL: ImportError — _detect_nonhardgate_model_drift does not exist yet.
    """
    from bytedigger_engine.llm_subprocess import _detect_nonhardgate_model_drift  # type: ignore[attr-defined]

    result = _detect_nonhardgate_model_drift(
        {"dispatched_model": "haiku"},
        None,
        False,
        "invoke_green_llm",
    )

    assert result is None, (
        f"AC9: _detect_nonhardgate_model_drift must return None when pinned_model is None; "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — end-to-end: non-hard-gate step with model drift emits "model_pin_mismatch"
# ---------------------------------------------------------------------------

def test_ac10_end_to_end_warn_event_emitted_non_blocking(
    tmp_path: Path, monkeypatch
) -> None:
    """AC10 (§1y integration): drive _invoke_in_session end-to-end for a non-hard-gate
    step (hard_gate=False) where the .res.json carries dispatched_model='haiku' but
    the command's pinned model is 'sonnet'.

    Asserts:
    (a) event_log contains a 'model_pin_mismatch' event with keys
        {observed_model, pinned_model, step_name, severity}.
    (b) StepResult.status == 'ok' (warn is non-blocking — must not fail the step).

    §1i (ABD52613 / workflows.md §1i): a daemon thread writes the .res.json AFTER
    the .req.json appears (post stale-sweep at llm_subprocess.py:549-553), avoiding
    the os.unlink race on pre-placed artifacts.

    Pre-GREEN FAIL: _invoke_in_session does not call _detect_nonhardgate_model_drift
    or emit 'model_pin_mismatch' — so either the event is absent (assertion a) or
    status is wrong (assertion b will still fail if step erroneously blocks).
    """
    from bytedigger_engine import llm_subprocess as _llm_mod

    # Set up request dir + RunContext.
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(request_dir))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="RUN-2FDA949D",
        step_name="invoke_green_llm",
        phase="phase_5",
    )

    # Pin the nonce so we know the exact artifact paths.
    fixed_nonce = "2fda949daabbccdd11223344"

    class _FakeUUID:
        hex = fixed_nonce

    monkeypatch.setattr(_llm_mod.uuid, "uuid4", staticmethod(lambda: _FakeUUID()))

    nonce_dir = request_dir / "RUN-2FDA949D" / "phase_5"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    request_path = nonce_dir / f"{fixed_nonce}.req.json"
    result_path = nonce_dir / f"{fixed_nonce}.res.json"

    # §1i: The daemon thread writes .res.json AFTER .req.json appears (post stale-sweep).
    # dispatched_model="haiku" while command pins "sonnet" → family drift → warn.
    res_payload = {
        "subtype": "success",
        "raw_response": "GREEN output text",
        "worker_written_paths": [],
        "manifest_source": "orchestrator_observed",
        "request_nonce": fixed_nonce,
        "__hal_integrity": "end",
        "dispatched_model": "haiku",  # drift: pinned is sonnet
    }
    runner_done = threading.Event()

    def _runner_thread() -> None:
        """Poll for request file, then atomically write result with dispatched_model."""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if request_path.exists():
                tmp_result = str(result_path) + ".tmp"
                with open(tmp_result, "w", encoding="utf-8") as f:
                    json.dump(res_payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_result, str(result_path))
                runner_done.set()
                return
            time.sleep(0.02)
        # Request never appeared — unblock main thread.
        runner_done.set()

    t = threading.Thread(target=_runner_thread, daemon=True)
    t.start()

    result = _invoke_in_session(
        prompt="implement the feature",
        model="sonnet",
        timeout_sec=5,
        step_name="invoke_green_llm",
        extra_data=None,
        allowed_tools=None,
        run_ctx=telemetry_ctx.get_current_run(),
        hard_gate=False,  # non-hard-gate step: drift should warn, not block
    )

    t.join(timeout=12.0)

    # (b) Non-blocking: the step must still succeed.
    assert result.status == "ok", (
        f"AC10(b): non-hard-gate model drift must NOT block the step; "
        f"expected status='ok', got {result.status!r} "
        f"(error_code={result.error_code!r})"
    )

    # (a) Warning event must be emitted.
    mismatch_events = log.by_type("model_pin_mismatch")
    assert len(mismatch_events) >= 1, (
        f"AC10(a): expected at least one 'model_pin_mismatch' event in event_log; "
        f"events captured: {[et for (et, _, _) in log.events]!r}"
    )

    ev = mismatch_events[0]
    for key in ("observed_model", "pinned_model", "step_name", "severity"):
        assert key in ev, (
            f"AC10(a): 'model_pin_mismatch' event payload missing key {key!r}; "
            f"payload keys: {list(ev.keys())!r}"
        )


# ---------------------------------------------------------------------------
# AC11 — regression: hard-gate downgrade still blocked by existing helper
# ---------------------------------------------------------------------------

def test_ac11_regression_hard_gate_downgrade_still_blocked() -> None:
    """AC11 (regression guard): _assert_in_session_model_or_downgrade with
    hard_gate=True and dispatched_model='sonnet' must still return a StepResult with
    error_code='E_HARD_GATE_MODEL_DOWNGRADE' and recoverable=False.

    This guard ensures the new non-hard-gate warn path does not inadvertently loosen
    the existing hard-gate enforcement.

    Pre-GREEN PASS: _assert_in_session_model_or_downgrade already exists and works
    correctly — this is a correctness guard that should PASS today and remain green
    post-GREEN.
    """
    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "n1",
        "__hal_integrity": "end",
        "dispatched_model": "sonnet",
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model="opus",
        hard_gate=True,
        step_name="validation",
        nonce="n1",
    )

    assert result is not None, (
        "AC11: _assert_in_session_model_or_downgrade must return a StepResult for "
        "hard_gate=True + dispatched sonnet (downgrade); got None"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"AC11: expected error_code='E_HARD_GATE_MODEL_DOWNGRADE'; "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC11: E_HARD_GATE_MODEL_DOWNGRADE must be recoverable=False; "
        f"got {result.recoverable!r}"
    )
