"""RED tests — GH901 agent-sdk backend usage/cost -> workflow_cost_rollup
ledger.

Spec: SHARED/memory/Decisions/2026-07-16_75EFC9B9_gh901_agent_sdk_cost_rollup_spec.md

12 tests, one per AC1-AC12 (§3 table). `_ledger_usage_fields` does not exist
yet on `lib.reference_backends.agent_sdk` (§2.1) — deferred import inside
test bodies for AC1-3 so the file COLLECTS cleanly and fails at
assert/import time (D1CF5FDF / §1q), not at collection.

§1i: fake `claude_agent_sdk` + file-backed event log are pre-staged
deterministically before invoking the backend — no timing races.
§1l stub-passability: only the external `claude_agent_sdk` package (an
external-dependency seam) and `run_ctx.event_log.append` (AC10) are
stubbed. `agent_sdk_backend`, `_ledger_usage_fields`, and the real consumer
`compute_cost_rollup` are exercised directly, unstubbed.
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass, field, fields
from typing import Any

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine.llm_subprocess import reset_backends
from bytedigger_engine.event_log import EventLog


# ---------------------------------------------------------------------------
# Deferred import (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_agent_sdk_backend():
    import bytedigger_engine.lib.reference_backends.agent_sdk as mod  # noqa: PLC0415  # bd#44: `import a.b.c as m` (not `from a.b import c`) — the test drops the module from sys.modules to force a re-execute, and `from` would hand back the stale attribute still bound on the parent package
    return mod


# ---------------------------------------------------------------------------
# Fake claude_agent_sdk — query() is an ASYNC GENERATOR (real sdk 0.2.120
# shape, #890/#894 lesson). ResultMessage mirrors real dataclass fields.
# ---------------------------------------------------------------------------

@dataclass
class ResultMessage:
    subtype: str = "success"
    duration_ms: int = 100
    is_error: bool = False
    num_turns: int = 1
    session_id: str | None = None
    total_cost_usd: float | None = None
    usage: dict | None = None
    result: str | None = None


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
        if "raise" in behavior:
            yield _assistant_like()
            raise behavior["raise"]
        yield _assistant_like()
        yield _assistant_like()
        if "result" in behavior:
            yield behavior["result"]
        else:
            yield ResultMessage(
                result="default output",
                usage={"input_tokens": 1, "output_tokens": 1},
                session_id="default-sid",
            )

    fake_mod = types.ModuleType("claude_agent_sdk")
    fake_mod.query = query
    fake_mod.ClaudeAgentOptions = ClaudeAgentOptions
    fake_mod.ResultMessage = ResultMessage
    fake_mod.recorded_calls = recorded_calls
    fake_mod.behavior_queue = behavior_queue
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_mod)
    return fake_mod


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root)] + list(args),
        check=True, capture_output=True, text=True,
    )


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "commit", "--allow-empty", "-q", "-m", "init")


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    reset_backends()
    sys.modules.pop("bytedigger_engine.lib.reference_backends.agent_sdk", None)
    sys.modules.pop("claude_agent_sdk", None)


def _make_run_ctx(tmp_path, run_id, phase=None, cycle=None, step_name="gate.step1"):
    log = EventLog(tmp_path / "events.jsonl")
    return types.SimpleNamespace(
        event_log=log, run_id=run_id, step_name=step_name, phase=phase, cycle=cycle,
    ), log


def _common_kwargs(root, run_ctx):
    return dict(
        model="claude-sonnet", timeout_sec=10,
        extra_data={"workspace_root": str(root)},
        allowed_tools=None, run_ctx=run_ctx, hard_gate=False, gate_label=None,
        straggler_cfg=None, idle_timeout_sec=None,
    )


# ---------------------------------------------------------------------------
# AC1 — dict usage -> exact four-key mapping
# ---------------------------------------------------------------------------

def test_ac1_ledger_usage_fields_dict_usage():
    mod = _import_agent_sdk_backend()
    usage = {
        "input_tokens": 5, "output_tokens": 7,
        "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13,
    }
    result = mod._ledger_usage_fields(usage)
    assert result == {
        "tokens_in": 5, "tokens_out": 7,
        "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13,
    }, f"AC1: unexpected mapping {result!r}"


# ---------------------------------------------------------------------------
# AC2 — attrs-style usage object -> same result
# ---------------------------------------------------------------------------

def test_ac2_ledger_usage_fields_attrs_style():
    mod = _import_agent_sdk_backend()

    class Usage:
        input_tokens = 5
        output_tokens = 7
        cache_creation_input_tokens = 11
        cache_read_input_tokens = 13

    result = mod._ledger_usage_fields(Usage())
    assert result == {
        "tokens_in": 5, "tokens_out": 7,
        "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13,
    }, f"AC2: unexpected mapping {result!r}"


# ---------------------------------------------------------------------------
# AC3 — None / missing / non-int -> all four None, never raises
# ---------------------------------------------------------------------------

def test_ac3_ledger_usage_fields_none_and_missing():
    mod = _import_agent_sdk_backend()

    none_result = mod._ledger_usage_fields(None)
    assert none_result == {
        "tokens_in": None, "tokens_out": None,
        "cache_creation_input_tokens": None, "cache_read_input_tokens": None,
    }, f"AC3: usage=None must yield all-None; got {none_result!r}"

    bad_result = mod._ledger_usage_fields({"input_tokens": "not-an-int"})
    assert bad_result == {
        "tokens_in": None, "tokens_out": None,
        "cache_creation_input_tokens": None, "cache_read_input_tokens": None,
    }, f"AC3: missing/non-int values must yield None; got {bad_result!r}"


# ---------------------------------------------------------------------------
# AC4 — success path emits exactly one runner_result_consumed row
# ---------------------------------------------------------------------------

def test_ac4_success_emits_one_runner_result_consumed_row(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="out1",
            usage={
                "input_tokens": 5, "output_tokens": 7,
                "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13,
            },
            session_id="s-1",
            total_cost_usd=0.42,
        )
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac4", phase="phase_5", cycle=2)

    result = mod.agent_sdk_backend(prompt="hello", step_name="gate.step1",
                                    **_common_kwargs(root, run_ctx))
    assert result.status == "ok", f"AC4: expected ok, got {result.status!r} err={result.error!r}"

    rows = [r for r in log.read_all() if r.get("event_type") == "runner_result_consumed"]
    assert len(rows) == 1, f"AC4: expected exactly 1 runner_result_consumed row; got {len(rows)}"
    payload = rows[0]["payload"]
    assert payload.get("backend") == "agent-sdk"
    assert payload.get("tokens_in") == 5
    assert payload.get("tokens_out") == 7
    assert payload.get("cache_creation_input_tokens") == 11
    assert payload.get("cache_read_input_tokens") == 13
    assert payload.get("cost_usd") == 0.42
    assert payload.get("phase") == "phase_5"
    assert payload.get("cycle") == 2
    assert payload.get("step_name")
    assert payload.get("warm_resumed") is False


# ---------------------------------------------------------------------------
# AC5 — real consumer (compute_cost_rollup) reads the emitted row
# ---------------------------------------------------------------------------

def test_ac5_compute_cost_rollup_reads_emitted_row(monkeypatch, tmp_path):
    from bytedigger_engine.lib.cost_rollup import compute_cost_rollup

    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="out1",
            usage={
                "input_tokens": 5, "output_tokens": 7,
                "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13,
            },
            session_id="s-1",
            total_cost_usd=0.42,
        )
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac5", phase="phase_5", cycle=2)

    result = mod.agent_sdk_backend(prompt="hello", step_name="gate.step1",
                                    **_common_kwargs(root, run_ctx))
    assert result.status == "ok"

    rollup = compute_cost_rollup(tmp_path / "events.jsonl", "run-ac5")
    assert rollup["calls"] == 1, f"AC5: expected calls==1; got {rollup!r}"
    assert rollup["tokens_in"] == 5
    assert rollup["tokens_out"] == 7
    assert rollup["cost_usd"] == pytest.approx(0.42)
    assert rollup["by_phase"].get("phase_5", {}).get("calls") == 1, f"AC5: {rollup!r}"
    assert rollup["by_cycle"].get("2", {}).get("calls") == 1, f"AC5: {rollup!r}"


# ---------------------------------------------------------------------------
# AC6 — StepResult.data carries cost_usd + cache fields
# ---------------------------------------------------------------------------

def test_ac6_stepresult_data_carries_cost_and_cache_fields(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="out1",
            usage={
                "input_tokens": 5, "output_tokens": 7,
                "cache_creation_input_tokens": 11, "cache_read_input_tokens": 13,
            },
            session_id="s-1",
            total_cost_usd=0.42,
        )
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    run_ctx, _log = _make_run_ctx(tmp_path, "run-ac6", phase="phase_5", cycle=2)

    result = mod.agent_sdk_backend(prompt="hello", step_name="gate.step1",
                                    **_common_kwargs(root, run_ctx))
    assert result.status == "ok"
    assert result.data.get("cost_usd") == 0.42, f"AC6: {result.data!r}"
    assert result.data.get("cache_creation_input_tokens") == 11, f"AC6: {result.data!r}"
    assert result.data.get("cache_read_input_tokens") == 13, f"AC6: {result.data!r}"


# ---------------------------------------------------------------------------
# AC7 — run_ctx=None -> status ok, no exception
# ---------------------------------------------------------------------------

def test_ac7_run_ctx_none_no_exception(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(result="out1", usage={"input_tokens": 1, "output_tokens": 1},
                                 session_id="s-1", total_cost_usd=0.1)
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    result = mod.agent_sdk_backend(
        prompt="hello", model="claude-sonnet", timeout_sec=10, step_name="gate.step1",
        extra_data={"workspace_root": str(root)}, allowed_tools=None, run_ctx=None,
        hard_gate=False, gate_label=None, straggler_cfg=None, idle_timeout_sec=None,
    )
    assert result.status == "ok", f"AC7: {result.status!r} {result.error!r}"


# ---------------------------------------------------------------------------
# AC8 — total_cost_usd absent -> None in payload/data, rollup still counts
# ---------------------------------------------------------------------------

def test_ac8_missing_total_cost_usd_none_but_rollup_counts_call(monkeypatch, tmp_path):
    from bytedigger_engine.lib.cost_rollup import compute_cost_rollup

    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="out1", usage={"input_tokens": 5, "output_tokens": 7},
            session_id="s-1", total_cost_usd=None,
        )
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    run_ctx, _log = _make_run_ctx(tmp_path, "run-ac8", phase="phase_5", cycle=1)

    result = mod.agent_sdk_backend(prompt="hello", step_name="gate.step1",
                                    **_common_kwargs(root, run_ctx))
    assert result.status == "ok"
    assert result.data.get("cost_usd") is None, f"AC8: {result.data!r}"

    rollup = compute_cost_rollup(tmp_path / "events.jsonl", "run-ac8")
    assert rollup["calls"] == 1, f"AC8: {rollup!r}"
    assert rollup["cost_usd"] == 0.0, f"AC8: {rollup!r}"


# ---------------------------------------------------------------------------
# AC9 — two sequential calls same key -> rollup counts both, both cycles
# ---------------------------------------------------------------------------

def test_ac9_two_sequential_calls_rollup_counts_both_cycles(monkeypatch, tmp_path):
    from bytedigger_engine.lib.cost_rollup import compute_cost_rollup

    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(result="out1", usage={"input_tokens": 10, "output_tokens": 5},
                                 session_id="sid-1", total_cost_usd=0.1)
    })
    fake.behavior_queue.append({
        "result": ResultMessage(result="out2", usage={"input_tokens": 3, "output_tokens": 2},
                                 session_id="sid-1", total_cost_usd=0.2)
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    log = EventLog(tmp_path / "events.jsonl")
    run_ctx1 = types.SimpleNamespace(event_log=log, run_id="run-ac9", step_name="gate.step1",
                                      phase="phase_5", cycle=1)
    run_ctx2 = types.SimpleNamespace(event_log=log, run_id="run-ac9", step_name="gate.step1",
                                      phase="phase_5", cycle=2)

    r1 = mod.agent_sdk_backend(prompt="p1", step_name="gate.step1",
                                **_common_kwargs(root, run_ctx1))
    r2 = mod.agent_sdk_backend(prompt="p2", step_name="gate.step1",
                                **_common_kwargs(root, run_ctx2))
    assert r1.status == "ok" and r2.status == "ok"
    assert r2.data.get("warm_resumed") is True, f"AC9: {r2.data!r}"

    rollup = compute_cost_rollup(tmp_path / "events.jsonl", "run-ac9")
    assert rollup["calls"] == 2, f"AC9: expected calls==2; got {rollup!r}"
    assert "1" in rollup["by_cycle"], f"AC9: {rollup!r}"
    assert "2" in rollup["by_cycle"], f"AC9: {rollup!r}"


# ---------------------------------------------------------------------------
# AC10 — event_log.append raises -> backend still returns status ok
# ---------------------------------------------------------------------------

def test_ac10_event_log_append_raises_backend_still_ok(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(result="out1", usage={"input_tokens": 1, "output_tokens": 1},
                                 session_id="s-1", total_cost_usd=0.1)
    })
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    class _BoomLog:
        def append(self, *a, **k):
            raise RuntimeError("append boom")

    run_ctx = types.SimpleNamespace(event_log=_BoomLog(), run_id="run-ac10",
                                     step_name="gate.step1", phase="phase_5", cycle=1)

    result = mod.agent_sdk_backend(prompt="hello", step_name="gate.step1",
                                    **_common_kwargs(root, run_ctx))
    assert result.status == "ok", f"AC10: {result.status!r} {result.error!r}"


# ---------------------------------------------------------------------------
# AC11 — real-shape contract (skips absent real sdk)
# ---------------------------------------------------------------------------

def test_ac11_real_result_message_shape_contract():
    pytest.importorskip("claude_agent_sdk")
    import claude_agent_sdk  # noqa: PLC0415
    import dataclasses  # noqa: PLC0415

    names = {f.name for f in dataclasses.fields(claude_agent_sdk.ResultMessage)}
    expected = {"usage", "total_cost_usd", "session_id", "result"}
    assert expected <= names, f"AC11: missing fields {expected - names}; got {names!r}"


# ---------------------------------------------------------------------------
# AC12 — error path: no emit at all
# ---------------------------------------------------------------------------

def test_ac12_error_path_no_runner_result_consumed_row(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("sdk exploded")})
    mod = _import_agent_sdk_backend()

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac12", phase="phase_5", cycle=1)

    result = mod.agent_sdk_backend(prompt="hello", step_name="gate.step1",
                                    **_common_kwargs(root, run_ctx))
    assert result.status == "error", f"AC12: expected error; got {result.status!r}"

    rows = [r for r in log.read_all() if r.get("event_type") == "runner_result_consumed"]
    assert len(rows) == 0, f"AC12: expected no runner_result_consumed rows; got {len(rows)}"
