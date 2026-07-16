"""RED tests for 02FF48F4 — close E_HARD_GATE_MODEL_DOWNGRADE in-session blind spot.

Spec: SHARED/memory/Decisions/2026-06-13_02FF48F4_model_pin_insession_gate_spec.md

ACs covered:
  AC1  — inspect.signature(_invoke_in_session).parameters contains "hard_gate"
  AC2  — integration: hard_gate=True forwarded to _invoke_in_session end-to-end
  AC3  — helper: hard_gate=True + dispatched_model="sonnet" → E_HARD_GATE_MODEL_DOWNGRADE
  AC4  — helper: hard_gate=True + no dispatched_model → E_HARD_GATE_MODEL_DOWNGRADE
  AC5  — helper: hard_gate=True + dispatched_model="claude-opus-4-8" → None
  AC6  — helper: hard_gate=False + dispatched_model="haiku" → None
  AC7  — AC3 StepResult .data["observed_model"]=="sonnet"; AC4 .data["observed_model"] is None

§1q (D1CF5FDF): `_assert_in_session_model_or_downgrade` does NOT exist yet — imported
  INSIDE each test body (deferred), so the file collects without error pre-GREEN.
  `_invoke_in_session` and `_is_opus_model` already exist — top-level import is safe.

§1l: ACs anchor on real returns from the real helpers with on-disk fixtures (AC2) or
  real dict fixtures (AC3-7). No mocking of the UUT symbols.

§1i (ABD52613 / workflows.md §1i): AC2 uses a daemon thread that polls for the
  .req.json to appear, then atomically writes the .res.json AFTER the engine's
  pre-wait stale sweep (llm_subprocess.py:461-462). This avoids the Opus-blocker
  where a pre-placed res.json is deleted by os.unlink before the poll loop starts.
  Pattern mirrors test_4C03CCED_ship1b_in_session.py AC7.
"""
from __future__ import annotations

import inspect
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
if str(ENGINE_ROOT / "workflows") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "workflows"))
if str(ENGINE_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "lib"))

# Top-level imports of already-existing symbols only.
from llm_subprocess import _invoke_in_session, _is_opus_model  # noqa: E402
import telemetry_ctx  # noqa: E402


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
# AC1 — _invoke_in_session signature must include "hard_gate" param
# ---------------------------------------------------------------------------

def test_ac1_invoke_in_session_has_hard_gate_param() -> None:
    """AC1: inspect.signature(_invoke_in_session).parameters contains 'hard_gate'.

    Pre-GREEN FAIL: _invoke_in_session currently has no hard_gate parameter.
    Fails with AssertionError: 'hard_gate' not in params.
    """
    params = inspect.signature(_invoke_in_session).parameters
    assert "hard_gate" in params, (
        f"AC1: _invoke_in_session must have 'hard_gate' param; "
        f"current params: {list(params.keys())!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — integration: hard_gate=True forwarded end-to-end through _invoke_in_session
# ---------------------------------------------------------------------------

def test_ac2_hard_gate_forwarded_through_invoke_in_session(
    tmp_path: Path, monkeypatch
) -> None:
    """AC2 (§1y integration): drive _invoke_in_session end-to-end. A background daemon
    thread watches for the .req.json to appear (written by _invoke_in_session after the
    stale-sweep), then atomically writes a .res.json containing dispatched_model='sonnet'.
    With hard_gate=True, _invoke_in_session must detect the non-Opus dispatch and return
    E_HARD_GATE_MODEL_DOWNGRADE.

    §1i (ABD52613 / workflows.md §1i): the res.json is written by the daemon thread
    AFTER the engine's pre-wait stale sweep (llm_subprocess.py:461-462). Pre-placing
    before the call would cause os.unlink to delete it before the poll loop starts.
    Pattern mirrors test_4C03CCED_ship1b_in_session.py AC7.

    Pre-GREEN FAIL: _invoke_in_session doesn't accept hard_gate → TypeError,
    OR (if somehow piped through) wouldn't gate on in-session path.
    """
    import llm_subprocess as _llm_mod

    # Set up request dir + RunContext.
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(request_dir))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="RUN-02FF48F4",
        step_name="validation",
        phase="phase_5",
    )

    # Control the nonce so the daemon thread knows exactly which paths to watch/write.
    # _invoke_in_session: nonce_dir = os.path.join(request_dir, run_id, phase_segment)
    fixed_nonce = "02ff48f4aabbccdd11223344"

    class _FakeUUID:
        hex = fixed_nonce

    monkeypatch.setattr(_llm_mod.uuid, "uuid4", staticmethod(lambda: _FakeUUID()))

    nonce_dir = request_dir / "RUN-02FF48F4" / "phase_5"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    request_path = nonce_dir / f"{fixed_nonce}.req.json"
    result_path = nonce_dir / f"{fixed_nonce}.res.json"

    # §1i: Daemon thread writes res.json AFTER the req.json appears (post stale-sweep).
    res_payload = {
        "subtype": "success",
        "raw_response": "OK — opus validation done",
        "worker_written_paths": [],
        "manifest_source": "orchestrator_observed",
        "request_nonce": fixed_nonce,
        "__hal_integrity": "end",
        "dispatched_model": "sonnet",
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
        # Request never appeared — set event so main thread is not stuck.
        runner_done.set()

    t = threading.Thread(target=_runner_thread, daemon=True)
    t.start()

    result = _invoke_in_session(
        prompt="verify this spec",
        model="opus",
        timeout_sec=5,
        step_name="validation",
        extra_data=None,
        allowed_tools=None,
        run_ctx=telemetry_ctx.get_current_run(),
        hard_gate=True,  # This kwarg doesn't exist yet — TypeError pre-GREEN
    )

    t.join(timeout=12.0)

    assert result.status == "error", (
        f"AC2: expected status='error' (downgrade gate); got {result.status!r}"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"AC2: expected error_code='E_HARD_GATE_MODEL_DOWNGRADE'; "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC2: E_HARD_GATE_MODEL_DOWNGRADE must be recoverable=False; "
        f"got {result.recoverable!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — helper: hard_gate=True + dispatched_model="sonnet" → error StepResult
# ---------------------------------------------------------------------------

def test_ac3_helper_sonnet_dispatched_returns_downgrade_error() -> None:
    """AC3: _assert_in_session_model_or_downgrade(hard_gate=True, result_obj with
    dispatched_model='sonnet', ...) must return a StepResult with
    error_code='E_HARD_GATE_MODEL_DOWNGRADE' and recoverable=False.

    Pre-GREEN FAIL: ImportError — _assert_in_session_model_or_downgrade does not exist.
    """
    # Deferred import per §1q (D1CF5FDF): symbol does not exist yet.
    from llm_subprocess import _assert_in_session_model_or_downgrade  # type: ignore[attr-defined]

    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "nonce123",
        "__hal_integrity": "end",
        "dispatched_model": "sonnet",
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model="opus",
        hard_gate=True,
        step_name="validation",
        nonce="nonce123",
    )

    assert result is not None, (
        "AC3: helper must return a StepResult when dispatched_model='sonnet' + hard_gate=True; "
        "got None"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"AC3: expected error_code='E_HARD_GATE_MODEL_DOWNGRADE'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC3: E_HARD_GATE_MODEL_DOWNGRADE must be recoverable=False; "
        f"got recoverable={result.recoverable!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — helper: hard_gate=True + no dispatched_model → error StepResult
# ---------------------------------------------------------------------------

def test_ac4_helper_missing_dispatched_model_returns_downgrade_error() -> None:
    """AC4: _assert_in_session_model_or_downgrade(hard_gate=True, result_obj with
    NO dispatched_model key) must return E_HARD_GATE_MODEL_DOWNGRADE.

    This is the ppba E5 silent-omission bug: servicer omits the field entirely.

    Pre-GREEN FAIL: ImportError — helper does not exist.
    """
    from llm_subprocess import _assert_in_session_model_or_downgrade  # type: ignore[attr-defined]

    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "nonce456",
        "__hal_integrity": "end",
        # intentionally no "dispatched_model" key
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model="opus",
        hard_gate=True,
        step_name="validation",
        nonce="nonce456",
    )

    assert result is not None, (
        "AC4: helper must return a StepResult when dispatched_model is absent + hard_gate=True "
        "(silent-omission = the ppba E5 bug); got None"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"AC4: expected error_code='E_HARD_GATE_MODEL_DOWNGRADE'; got {result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — helper: hard_gate=True + dispatched_model="claude-opus-4-8" → None (pass)
# ---------------------------------------------------------------------------

def test_ac5_helper_opus_dispatched_returns_none() -> None:
    """AC5: _assert_in_session_model_or_downgrade(hard_gate=True,
    dispatched_model='claude-opus-4-8') must return None (gate passes, no downgrade).

    Pre-GREEN FAIL: ImportError — helper does not exist.
    """
    from llm_subprocess import _assert_in_session_model_or_downgrade  # type: ignore[attr-defined]

    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "nonce789",
        "__hal_integrity": "end",
        "dispatched_model": "claude-opus-4-8",
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model="opus",
        hard_gate=True,
        step_name="validation",
        nonce="nonce789",
    )

    assert result is None, (
        f"AC5: helper must return None when dispatched_model='claude-opus-4-8' + hard_gate=True "
        f"(Opus model passes the gate); got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — helper: hard_gate=False + any dispatched_model → None (gate skip)
# ---------------------------------------------------------------------------

def test_ac6_helper_hard_gate_false_returns_none() -> None:
    """AC6: _assert_in_session_model_or_downgrade(hard_gate=False, ...) must return
    None regardless of dispatched_model (cheap mechanical step unaffected).

    Pre-GREEN FAIL: ImportError — helper does not exist.
    """
    from llm_subprocess import _assert_in_session_model_or_downgrade  # type: ignore[attr-defined]

    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "nonceABC",
        "__hal_integrity": "end",
        "dispatched_model": "haiku",
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model="haiku",
        hard_gate=False,
        step_name="some_cheap_step",
        nonce="nonceABC",
    )

    assert result is None, (
        f"AC6: helper must return None when hard_gate=False (gate bypassed); "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — error StepResult .data fields: observed_model and pinned_model
# ---------------------------------------------------------------------------

def test_ac7_error_stepresult_data_fields_ac3_sonnet() -> None:
    """AC7 (AC3 branch): StepResult.data['observed_model']=='sonnet' and
    data['pinned_model']=='opus' when dispatched_model='sonnet'.

    Pre-GREEN FAIL: ImportError — helper does not exist.
    """
    from llm_subprocess import _assert_in_session_model_or_downgrade  # type: ignore[attr-defined]

    pinned = "opus"
    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "nonce_ac7_1",
        "__hal_integrity": "end",
        "dispatched_model": "sonnet",
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model=pinned,
        hard_gate=True,
        step_name="validation",
        nonce="nonce_ac7_1",
    )

    assert result is not None, (
        "AC7/AC3: expected non-None StepResult for sonnet downgrade; got None"
    )
    data = result.data
    assert data is not None, f"AC7: StepResult.data must not be None; got {data!r}"
    assert data.get("observed_model") == "sonnet", (
        f"AC7: data['observed_model'] must be 'sonnet'; got {data.get('observed_model')!r}"
    )
    assert data.get("pinned_model") == pinned, (
        f"AC7: data['pinned_model'] must be {pinned!r}; got {data.get('pinned_model')!r}"
    )


def test_ac7_error_stepresult_data_fields_ac4_none_model() -> None:
    """AC7 (AC4 branch): StepResult.data['observed_model'] is None when
    dispatched_model is absent from result_obj (silent-omission case).

    Pre-GREEN FAIL: ImportError — helper does not exist.
    """
    from llm_subprocess import _assert_in_session_model_or_downgrade  # type: ignore[attr-defined]

    pinned = "opus"
    result_obj = {
        "subtype": "success",
        "raw_response": "ok",
        "request_nonce": "nonce_ac7_2",
        "__hal_integrity": "end",
        # no dispatched_model key
    }

    result = _assert_in_session_model_or_downgrade(
        result_obj=result_obj,
        pinned_model=pinned,
        hard_gate=True,
        step_name="validation",
        nonce="nonce_ac7_2",
    )

    assert result is not None, (
        "AC7/AC4: expected non-None StepResult for missing dispatched_model; got None"
    )
    data = result.data
    assert data is not None, f"AC7: StepResult.data must not be None; got {data!r}"
    assert data.get("observed_model") is None, (
        f"AC7: data['observed_model'] must be None when dispatched_model absent; "
        f"got {data.get('observed_model')!r}"
    )
    assert data.get("pinned_model") == pinned, (
        f"AC7: data['pinned_model'] must be {pinned!r}; got {data.get('pinned_model')!r}"
    )
