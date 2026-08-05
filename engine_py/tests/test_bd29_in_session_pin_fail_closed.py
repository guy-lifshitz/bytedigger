"""bd#29 — in-session model-pin fail-closed flip (supersede 220E5F63).

Spec: `docs/decisions/2026-08-04-bd29-in-session-model-pin-fail-closed.md`.

Class: declaration without measurement (gate B-3 of bd#10). The chokepoint
`_pin_mismatch_refusal` (`llm_subprocess.py:1088`, called from
`invoke_llm_subprocess` at `:1301`) ALREADY stands on the path of every backend. It reads
`StepResult.data["observed_model"]`. `_invoke_in_session` does not write that key
(`:939-951`), so on an in-session result the chokepoint gets `observed=None`
and comes out `not-checked`, while the parallel warn-only branch (`:929-932`) emits
a warning and lets the step through. The shield is green while the hole is open.

⇒ The flip must be a CONSEQUENCE of the chokepoint, not a second implementation of the check:
it is enough to fill the channel and remove the superseded warn-only call.

WHY THESE LEGS RUN `invoke_llm_subprocess` AND NOT `_invoke_in_session`.
The live AC10 (`test_2FDA949D_model_pin_warn.py`) calls `_invoke_in_session`
DIRECTLY. Copying that form would mean writing an AC that can never
go green: the chokepoint lives ONE LEVEL UP, inside `invoke_llm_subprocess`.
So the legs enter through the public entry point with `backend="claude-in-session"` — that
is the in-session path end-to-end, and it is the only one that reaches adjudication.

The exposure was measured BEFORE RED (spec §1): 22 production sites, of which 15 are non-hard-gate
(that is the warn-only exposure), 7 hard-gate, and ZERO sites without a model pin.
The default backend is `agent-sdk`; in-session is enabled only by an explicit opt-in.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess

from test_2FDA949D_model_pin_warn import _FakeEventLog

NONCE = "bd29aabbccdd112233445566"


def _drive_in_session(
    tmp_path: Path,
    monkeypatch,
    *,
    dispatched_model: "str | None",
    pinned_model: str = "sonnet",
    hard_gate: bool = False,
):
    """Run an in-session step end-to-end through the PUBLIC entry point.

    Returns (StepResult, _FakeEventLog). The daemon thread writes `.res.json` AFTER
    `.req.json` appears (§1i / ABD52613) — otherwise the stale sweep removes an artefact
    laid down in advance.
    """
    from bytedigger_engine import llm_subprocess as _llm_mod

    request_dir = tmp_path / "requests"
    request_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(request_dir))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-BD29", step_name="invoke_step", phase="phase_5",
    )

    class _FakeUUID:
        hex = NONCE

    monkeypatch.setattr(_llm_mod.uuid, "uuid4", staticmethod(lambda: _FakeUUID()))

    nonce_dir = request_dir / "RUN-BD29" / "phase_5"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    request_path = nonce_dir / f"{NONCE}.req.json"
    result_path = nonce_dir / f"{NONCE}.res.json"

    payload = {
        "subtype": "success",
        "raw_response": "worker output",
        "worker_written_paths": [],
        "manifest_source": "orchestrator_observed",
        "request_nonce": NONCE,
        "__hal_integrity": "end",
    }
    if dispatched_model is not None:
        payload["dispatched_model"] = dispatched_model

    def _runner() -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if request_path.exists():
                tmp_result = str(result_path) + ".tmp"
                with open(tmp_result, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_result, str(result_path))
                return
            time.sleep(0.02)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    try:
        result = invoke_llm_subprocess(
            prompt="do the thing",
            model=pinned_model,
            timeout_sec=5,
            step_name="invoke_step",
            hard_gate=hard_gate,
            backend="claude-in-session",
        )
    finally:
        t.join(timeout=12.0)
    return result, log


# ─── AC1: the subject — drift on a non-hard-gate step must fail the step ──

def test_ac1_non_hardgate_drift_fails_closed(tmp_path, monkeypatch):
    """CL:99: a declared pin MUST fail on mismatch.

    Red today: the path emits `model_pin_mismatch` and returns `ok`
    (220E5F63 / GH#222).
    """
    result, _log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="haiku")

    assert result.status == "error", (
        f"model drift on a non-hard-gate in-session step must fail the step "
        f"(CL:99), got status={result.status!r}"
    )
    assert result.error_code == "E_MODEL_PIN_MISMATCH", (
        f"expected E_MODEL_PIN_MISMATCH, got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        "a pin mismatch is not fixable by a retry — recoverable must be False"
    )


# ─── AC2: the surviving halves of AC10, re-anchored in a NEW oracle ──────

def test_ac2_mismatch_event_still_emitted_with_its_keys(tmp_path, monkeypatch):
    """The issue's item 4: editing a live regression test to the
    opposite outcome would mean the change is judged by an artefact
    the lot edits itself. The surviving AC10 asserts are re-anchored here.
    """
    _result, log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="haiku")

    events = log.by_type("model_pin_mismatch")
    assert len(events) >= 1, (
        f"the model_pin_mismatch event must still be emitted; "
        f"captured: {[et for (et, _, _) in log.events]!r}"
    )
    ev = events[0]
    for key in ("observed_model", "pinned_model", "step_name"):
        assert key in ev, (
            f"the model_pin_mismatch payload lost the key {key!r}; keys: {list(ev.keys())!r}"
        )
    assert ev["observed_model"] == "haiku"
    assert ev["pinned_model"] == "sonnet"


# ─── AC3: NEGATIVE LEG — the channel, not a second check ─────────────────

def test_ac3_in_session_result_exposes_observed_model_channel(tmp_path, monkeypatch):
    """Without this leg the "flip" could have been done by a second check INSIDE
    `_invoke_in_session`, leaving the channel empty — i.e. reproducing defect
    B-3 in the guise of a fix. Here the channel itself is pinned, on a run WITHOUT drift.
    """
    result, _log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="sonnet")

    assert isinstance(result.data, dict), f"expected dict data, got {type(result.data)}"
    assert result.data.get("observed_model") == "sonnet", (
        f"an in-session result must carry observed_model from dispatched_model — "
        f"this is the channel the chokepoint reads; got "
        f"{result.data.get('observed_model')!r}"
    )


# ─── AC4: NEGATIVE LEG — a fail-closed that never fails does not exist ───

def test_ac4_no_drift_still_passes_and_emits_nothing(tmp_path, monkeypatch):
    """The control: a "fix" that fails everything indiscriminately would be green on AC1."""
    result, log = _drive_in_session(tmp_path, monkeypatch, dispatched_model="sonnet")

    assert result.status == "ok", (
        f"matching families — the step must pass, got "
        f"status={result.status!r} error_code={result.error_code!r}"
    )
    assert log.by_type("model_pin_mismatch") == [], (
        "without drift the model_pin_mismatch event must not be emitted"
    )


# ─── AC5: the bd#10 boundary is not moved (D4) ───────────────────────────

def test_ac5_absent_dispatched_model_stays_not_checked(tmp_path, monkeypatch):
    """bd#10 decided explicitly: "an adapter that reported nothing ... is not-checked".

    bd#29 does NOT overturn that decision. The leg guards against the flip spreading into
    fail-closed on a missing report — that would be a different subject with a different argument.
    """
    result, log = _drive_in_session(tmp_path, monkeypatch, dispatched_model=None)

    assert result.status == "ok", (
        f"a missing dispatched_model is not-checked, not a refusal; got "
        f"{result.status!r} / {result.error_code!r}"
    )
    assert log.by_type("model_pin_mismatch") == []


# ─── AC7: the requirement label declares the new state (D5) ──────────────

def test_ac7_requirement_label_declares_chokepoint_enforced():
    """The supersession of 220E5F63 must be VISIBLE to the attestation, not only in the code."""
    from bytedigger_engine.conformance.attest import REQUIREMENT_LABELS

    assert REQUIREMENT_LABELS["R3.3"] == "chokepoint-enforced", (
        f"the R3.3 label must declare the new state, got "
        f"{REQUIREMENT_LABELS['R3.3']!r}"
    )


# ─── AC6: the hard-gate path is untouched ────────────────────────────────

def test_ac6_hard_gate_drift_still_handled_by_its_own_guard(tmp_path, monkeypatch):
    """hard_gate=True is already fail-closed via `_assert_in_session_model_or_downgrade`
    (02FF48F4). The flip has no right to replace its refusal code with its own —
    otherwise two guards on one path start arguing about the error code.
    """
    result, _log = _drive_in_session(
        tmp_path, monkeypatch, dispatched_model="haiku", hard_gate=True,
    )

    assert result.status == "error", (
        f"hard-gate drift must fail the step, got {result.status!r}"
    )
    # A MEASUREMENT, not an expectation: today E_MODEL_PIN_MISMATCH arrives here, even though
    # the refusal comes from `_assert_in_session_model_or_downgrade` with ITS OWN code
    # E_HARD_GATE_MODEL_DOWNGRADE. Its refusal carries data["observed_model"], and
    # the chokepoint at :1301 reads that key, sees the family drift and OVERWRITES
    # the result with its own code. That is, the hard-gate guard's code does not reach
    # the caller when the families additionally differ.
    #
    # This is a PRE-EXISTING defect (two guards on one path, the later overwriting
    # the earlier), it is NOT created by bd#29 and is NOT fixed here — that would be a second
    # subject. The leg pins today's observable behaviour so that the flip does not
    # shift it unnoticed; when the defect is fixed, this line must
    # fail and demand a decision.
    assert result.error_code == "E_MODEL_PIN_MISMATCH", (
        f"hard-gate behaviour has shifted: expected today's "
        f"E_MODEL_PIN_MISMATCH (the chokepoint overwrites E_HARD_GATE_MODEL_DOWNGRADE), "
        f"got {result.error_code!r}"
    )
