"""RED tests — agent_sdk backend: salvage success ResultMessage on trailing
stream error (GH956).

Spec: SHARED/memory/Decisions/2026-07-17_GH956_agent_sdk_success_salvage_spec.md

10 tests, one per AC1-AC10 (§3 table). `_salvage_success_result` does not
exist yet on `lib.reference_backends.agent_sdk` — deferred access (getattr /
call) inside test bodies so the file COLLECTS cleanly and FAILS at assert
time, not at collection/import time (D1CF5FDF / §1q).

§1q: no `spec_from_file_location` / `exec_module`; no module-level
`sys.path` mutation; no `from conftest import`.
§1i: fake `claude_agent_sdk` + file-backed event log + HAL_OUTAGE_PROBE=0
are pre-staged deterministically (monkeypatch) BEFORE invoking the backend —
no timing races, no network probe.
§1l stub-passability: only the external `claude_agent_sdk` module (sys.modules
injection, matches GH933/GH885 sibling pattern) is faked. The UUT
(`agent_sdk_backend`, `_salvage_success_result`) is exercised directly,
unstubbed.

Expected pre-GREEN: AC5/AC6 PASS (existing except-branch behavior is
unaffected by success-then-raise / is_error=True cases), AC1-AC4 and
AC7-AC10 FAIL (salvage does not exist yet) -> 8 FAIL / 2 PASS.
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass

import pytest

from event_log import EventLog


# ---------------------------------------------------------------------------
# Deferred import (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_agent_sdk_backend():
    import lib.reference_backends.agent_sdk as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# Fake claude_agent_sdk — query() is an ASYNC GENERATOR (real SDK shape,
# GH894/GH933/GH885 sibling pattern). ResultMessage carries an `is_error`
# attribute (new for this spec, §3 fixture pattern).
# ---------------------------------------------------------------------------

@dataclass
class ResultMessage:
    result: str | None = None
    usage: dict | None = None
    session_id: str | None = None
    is_error: bool = False


def _install_fake_agent_sdk(monkeypatch):
    recorded_calls = []
    behavior_queue = []

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.kwargs = kwargs

    def _assistant_like():
        return types.SimpleNamespace(kind="assistant-like")

    async def query(*, prompt, options):
        recorded_calls.append(options)
        behavior = behavior_queue.pop(0) if behavior_queue else {}
        yield _assistant_like()
        if "result" in behavior:
            yield behavior["result"]
        if "raise" in behavior:
            raise behavior["raise"]

    fake_mod = types.ModuleType("claude_agent_sdk")
    fake_mod.query = query
    fake_mod.ClaudeAgentOptions = ClaudeAgentOptions
    fake_mod.ResultMessage = ResultMessage
    fake_mod.recorded_calls = recorded_calls
    fake_mod.behavior_queue = behavior_queue
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_mod)
    return fake_mod


def _success_then_raise_behavior():
    """§3 fixture: query yields assistant-like, ResultMessage(success,
    is_error=False), then raises the exact "…error result: success" trailing
    ProcessError-rewrite text.
    """
    return {
        "result": ResultMessage(
            result="SALVAGE_OK_TEXT",
            usage={"input_tokens": 3, "output_tokens": 2},
            session_id="sess-salvage-1",
            is_error=False,
        ),
        "raise": RuntimeError("Claude Code returned an error result: success"),
    }


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root)] + list(args),
        check=True, capture_output=True, text=True,
    )


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "commit", "--allow-empty", "-q", "-m", "init")


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    return root


@pytest.fixture(autouse=True)
def _reset_state():
    """Snapshot/restore `_SESSION_CACHE` across tests (monkeypatch-style
    dict swap) so no cache entry leaks between test functions; also drop the
    two dynamically-loaded modules per test (sibling pattern).
    """
    yield
    sys.modules.pop("lib.reference_backends.agent_sdk", None)
    sys.modules.pop("claude_agent_sdk", None)


@pytest.fixture(autouse=True)
def _disable_outage_probe(monkeypatch):
    """§1i: pre-stage the outage-probe kill-switch BEFORE invoking the
    backend so the except-branch path never hits the network (offline)."""
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")


def _common_kwargs(root, run_ctx=None):
    return dict(
        model="claude-sonnet",
        extra_data={"workspace_root": str(root)},
        allowed_tools=None,
        run_ctx=run_ctx,
        hard_gate=False,
        gate_label=None,
        straggler_cfg=None,
        idle_timeout_sec=None,
    )


def _make_run_ctx(tmp_path, run_id, step_name="gate.step1"):
    log = EventLog(tmp_path / "events.jsonl")
    return types.SimpleNamespace(
        event_log=log, run_id=run_id, step_name=step_name, phase=None, cycle=None,
    ), log


# ---------------------------------------------------------------------------
# AC1 — success-then-raise -> StepResult.status == "ok" (not error)
# ---------------------------------------------------------------------------

def test_ac1_success_then_raise_returns_ok_status(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append(_success_then_raise_behavior())
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.status == "ok", (
        f"AC1: a successful ResultMessage received before a trailing stream "
        f"error must be salvaged into a StepResult with status='ok'; got "
        f"{result.status!r} error_code={result.error_code!r} error={result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — data['raw_response'] == 'SALVAGE_OK_TEXT'
# ---------------------------------------------------------------------------

def test_ac2_salvaged_raw_response_from_result_message(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append(_success_then_raise_behavior())
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.data is not None and result.data.get("raw_response") == "SALVAGE_OK_TEXT", (
        f"AC2: data['raw_response'] must come from the salvaged ResultMessage; "
        f"got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — data['salvaged_stream_error'] is a str containing 'error result: success'
# ---------------------------------------------------------------------------

def test_ac3_salvaged_stream_error_records_trailing_exception_text(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append(_success_then_raise_behavior())
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.data is not None
    salvaged_err = result.data.get("salvaged_stream_error")
    assert isinstance(salvaged_err, str) and "error result: success" in salvaged_err, (
        f"AC3: data['salvaged_stream_error'] must be a str containing the "
        f"trailing exception text 'error result: success'; got {salvaged_err!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — runner_result_consumed emitted with outcome=='success' AND salvaged is True
# ---------------------------------------------------------------------------

def test_ac4_event_log_records_success_outcome_and_salvaged_true(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append(_success_then_raise_behavior())
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac4")

    mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    rows = [r for r in log.read_all() if r.get("event_type") == "runner_result_consumed"]
    assert len(rows) == 1, (
        f"AC4: expected exactly 1 runner_result_consumed row emitted from the "
        f"salvaged success tail; got {len(rows)}"
    )
    payload = rows[0].get("payload", {})
    assert payload.get("outcome") == "success", (
        f"AC4: payload['outcome'] must be 'success'; got {payload!r}"
    )
    assert payload.get("salvaged") is True, (
        f"AC4: payload['salvaged'] must be True on a salvaged success tail; "
        f"got {payload!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — query raises BEFORE any ResultMessage -> unchanged error behavior
# ---------------------------------------------------------------------------

def test_ac5_raise_before_any_result_message_stays_error(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("boom, no result ever arrived")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.status == "error", (
        f"AC5: with no ResultMessage ever received, the run must stay a "
        f"genuine error; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_API_BAD_RESPONSE", (
        f"AC5: expected 'E_LLM_API_BAD_RESPONSE'; got {result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — ResultMessage with is_error=True then raise -> no salvage
# ---------------------------------------------------------------------------

def test_ac6_is_error_true_result_message_is_not_salvaged(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="NOT_SALVAGEABLE",
            usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-error-1",
            is_error=True,
        ),
        "raise": RuntimeError("Claude Code returned an error result: error"),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.status == "error", (
        f"AC6: an is_error=True ResultMessage must NOT be salvaged; got "
        f"{result.status!r}"
    )
    assert result.error_code == "E_LLM_API_BAD_RESPONSE", (
        f"AC6: expected 'E_LLM_API_BAD_RESPONSE'; got {result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — success-path session-cache write preserved on the salvage path
# ---------------------------------------------------------------------------

def test_ac7_salvage_path_still_caches_session(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append(_success_then_raise_behavior())
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx = types.SimpleNamespace(run_id="r1")

    mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="phase_5.x", **_common_kwargs(root, run_ctx)
    )

    assert mod._SESSION_CACHE.get("r1:phase_5") == ("sess-salvage-1", 0), (
        f"AC7: salvage tail must reuse the normal success-path cache write; "
        f"got {mod._SESSION_CACHE!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — _salvage_success_result: direct helper behavior
# ---------------------------------------------------------------------------

def test_ac8_salvage_helper_direct_behavior(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    helper = getattr(mod, "_salvage_success_result", None)
    assert callable(helper), (
        "AC8: _salvage_success_result must exist as a module-level callable"
    )

    assert helper({"msg": None}) is None, (
        "AC8: an empty holder (no message ever received) must salvage to None"
    )

    success_msg = ResultMessage(
        result="ok-text", usage={"input_tokens": 1, "output_tokens": 1},
        session_id="sid", is_error=False,
    )
    assert helper({"msg": success_msg}) is success_msg, (
        "AC8: a holder with a non-error ResultMessage must salvage to that message"
    )

    error_msg = ResultMessage(
        result="bad-text", usage={"input_tokens": 1, "output_tokens": 1},
        session_id="sid", is_error=True,
    )
    assert helper({"msg": error_msg}) is None, (
        "AC8: a holder with an is_error=True ResultMessage must salvage to None"
    )


# ---------------------------------------------------------------------------
# AC9 — clean run: no salvaged_stream_error key, no telemetry 'salvaged' key
# ---------------------------------------------------------------------------

def test_ac9_clean_run_has_no_additive_salvage_keys(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="clean-output",
            usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sid-clean",
            is_error=False,
        ),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac9")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok"
    assert result.data is not None and "salvaged_stream_error" not in result.data, (
        f"AC9: a clean (no-raise) run must NOT carry the additive "
        f"'salvaged_stream_error' key; got {result.data!r}"
    )
    rows = [r for r in log.read_all() if r.get("event_type") == "runner_result_consumed"]
    assert len(rows) == 1
    payload = rows[0].get("payload", {})
    assert "salvaged" not in payload, (
        f"AC9: a clean-path telemetry payload must NOT carry the additive "
        f"'salvaged' key; got {payload!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — duration_ms >= 0 int and no cache invalidation on the salvage path
# ---------------------------------------------------------------------------

def test_ac10_salvage_path_duration_and_no_cache_invalidation(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append(_success_then_raise_behavior())
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx = types.SimpleNamespace(run_id="r1")

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="phase_5.x", **_common_kwargs(root, run_ctx)
    )

    assert isinstance(result.duration_ms, int) and result.duration_ms >= 0, (
        f"AC10: duration_ms must be a non-negative int; got {result.duration_ms!r}"
    )
    assert mod._SESSION_CACHE.get("r1:phase_5") == ("sess-salvage-1", 0), (
        f"AC10: the salvage path must NOT invalidate the cache key it just "
        f"wrote (no _invalidate call on salvage); got {mod._SESSION_CACHE!r}"
    )
