"""RED tests — agent_sdk backend: retry-with-backoff + observability (GH1157 Fix#1).

Spec: SHARED/memory/Decisions/2026-07-24_GH1157_agent_sdk_concurrency_fix_spec.md

12 tests, one per AC1-AC12 (spec §3 table). `_is_retryable_agent_sdk_failure`,
`_backoff_delay_sec`, `_max_sdk_attempts` do not exist yet on
`lib.reference_backends.agent_sdk` — deferred access (getattr / call) inside
test bodies so the file COLLECTS cleanly and FAILS at assert time, not at
collection/import time (D1CF5FDF / §1q).

§1q: no `spec_from_file_location` / `exec_module`; no module-level
`sys.path` mutation; no `from conftest import`.
§1i: fake `claude_agent_sdk`, file-backed event log, HAL_OUTAGE_PROBE=0 and
backoff env knobs are pre-staged deterministically (monkeypatch) BEFORE
invoking the backend — no timing races, no network probe.
§1l stub-passability: only the external `claude_agent_sdk` module (sys.modules
injection, matches GH933/GH885/GH956 sibling pattern) is faked. The UUT
(`agent_sdk_backend` and the new retry/classifier helpers) is exercised
directly, unstubbed.

Expected pre-GREEN: 12 FAIL / 0 PASS / 0 collect errors — none of the retry
machinery, `exit_code`/`retry_attempts`/`silent_no_result` data keys, or the
`agent_sdk_retry` telemetry event exist yet.
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass

import pytest

from bytedigger_engine.event_log import EventLog


# ---------------------------------------------------------------------------
# Deferred import (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_agent_sdk_backend():
    from bytedigger_engine.lib.reference_backends import agent_sdk as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# Fake claude_agent_sdk — query() is an ASYNC GENERATOR (real SDK shape,
# GH894/GH933/GH885/GH956 sibling pattern). Each call to query() pops the
# next behavior off a shared queue, so multi-attempt retry sequences can be
# scripted call-by-call.
# ---------------------------------------------------------------------------

@dataclass
class ResultMessage:
    result: str | None = None
    usage: dict | None = None
    session_id: str | None = None
    is_error: bool = False


class RetryableExc(Exception):
    def __init__(self, msg, status_code=None, exit_code=None, stderr=None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code
        if exit_code is not None:
            self.exit_code = exit_code
        if stderr is not None:
            self.stderr = stderr


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
        if behavior.get("hang"):
            import asyncio  # noqa: PLC0415
            await asyncio.Event().wait()
            return
        yield _assistant_like()
        if "stderr_lines" in behavior:
            for line in behavior["stderr_lines"]:
                options.kwargs.get("stderr", lambda _l: None)(line)
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
    _KEYS = ("lib.reference_backends.agent_sdk", "claude_agent_sdk")
    _saved = {k: sys.modules.get(k) for k in _KEYS}
    yield
    for k in _KEYS:
        orig = _saved[k]
        if orig is not None:
            sys.modules[k] = orig
        else:
            sys.modules.pop(k, None)


@pytest.fixture(autouse=True)
def _pre_stage_env(monkeypatch):
    """§1i: pre-stage outage-probe kill-switch + tiny deterministic backoff
    envs BEFORE invoking the backend — no network, no unbounded sleeps."""
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")
    monkeypatch.setenv("HAL_AGENT_SDK_BACKOFF_BASE_SEC", "0.001")
    monkeypatch.setenv("HAL_AGENT_SDK_BACKOFF_CAP_SEC", "0.002")


@pytest.fixture(autouse=True)
def _record_sleeps(monkeypatch):
    """Pass-through recorder on asyncio.sleep so retry-loop delays are
    observable without actually waiting real wall-clock time in tests."""
    import asyncio

    recorded = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *a, **kw):
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return recorded


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
# AC1 — result-None sink enriched: data is dict, stderr_tail present
# ---------------------------------------------------------------------------

def test_ac1_none_result_sink_attaches_stderr_tail(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"stderr_lines": ["boring diagnostic line one"]})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.status == "error" and result.error_code == "E_LLM_API_BAD_RESPONSE"
    assert isinstance(result.data, dict), (
        f"AC1: result-None sink must attach a dict data (not None); got {result.data!r}"
    )
    assert "boring diagnostic line one" in result.data.get("stderr_tail", ""), (
        f"AC1: data['stderr_tail'] must contain the fed stderr lines; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — exit_code == 0, silent_no_result True, retry_attempts == 1
# ---------------------------------------------------------------------------

def test_ac2_none_result_sink_exit_code_and_attempts(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"stderr_lines": ["boring diagnostic line one"]})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.data is not None
    assert result.data.get("exit_code") == 0, f"AC2: expected exit_code==0; got {result.data!r}"
    assert result.data.get("silent_no_result") is True, (
        f"AC2: expected silent_no_result True; got {result.data!r}"
    )
    assert result.data.get("retry_attempts") == 1, (
        f"AC2: non-retryable stderr must NOT retry -> retry_attempts==1; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — exception sink exit_code duck-typed from exc.exit_code attr
# ---------------------------------------------------------------------------

def test_ac3_exception_sink_exit_code_duck_typed(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "raise": RetryableExc("boom, non-retryable text here", exit_code=1)
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )
    assert result.data is not None
    assert result.data.get("exit_code") == 1, (
        f"AC3: exc.exit_code attr must be duck-typed onto data['exit_code']; got {result.data!r}"
    )

    fake.behavior_queue.append({"raise": RuntimeError("boom, non-retryable, no attr")})
    result2 = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )
    assert result2.data is not None
    assert result2.data.get("exit_code") is None, (
        f"AC3: missing exit_code attr must yield data['exit_code'] is None; got {result2.data!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — classifier truth-table
# ---------------------------------------------------------------------------

def test_ac4_classifier_truth_table(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    classify = getattr(mod, "_is_retryable_agent_sdk_failure", None)
    assert callable(classify), (
        "AC4: _is_retryable_agent_sdk_failure must exist as a module-level callable"
    )

    for code in (429, 503, 529):
        exc = RetryableExc("x", status_code=code)
        assert classify(exc, "") is True, f"AC4: status_code={code} must classify retryable"

    for text in (
        "429 too many requests", "rate limit", "Overloaded",
        "ECONNRESET", "socket hang up",
    ):
        assert classify(None, text) is True, f"AC4: text {text!r} must classify retryable"

    for text in ("invalid api key", "spec malformed", ""):
        assert classify(None, text) is False, f"AC4: text {text!r} must classify NON-retryable"


# ---------------------------------------------------------------------------
# AC5 — always-retryable exc exhausts attempt budget then fails classified-fatal
# ---------------------------------------------------------------------------

def test_ac5_exhausts_attempt_budget_on_persistent_429(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    for _ in range(5):
        fake.behavior_queue.append({
            "raise": RetryableExc("429 rate limit hit", status_code=429)
        })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    max_attempts = getattr(mod, "_max_sdk_attempts", lambda: None)()
    assert max_attempts == 3, f"AC5: default _max_sdk_attempts() must be 3; got {max_attempts!r}"

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert len(fake.recorded_calls) == max_attempts, (
        f"AC5: query() must be invoked exactly _max_sdk_attempts()={max_attempts} times; "
        f"got {len(fake.recorded_calls)}"
    )
    assert result.status == "error" and result.error_code == "E_LLM_API_BAD_RESPONSE"
    assert result.recoverable is True
    assert result.data is not None and result.data.get("retry_attempts") == max_attempts, (
        f"AC5: data['retry_attempts'] must equal the attempt budget; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — retry then success -> ok, retry_attempts==2, resume=None on retry attempt
# ---------------------------------------------------------------------------

def test_ac6_retry_then_success_resets_resume(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RetryableExc("429 rate limit", status_code=429)})
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="OK_AFTER_RETRY", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-retry-ok", is_error=False,
        ),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx = types.SimpleNamespace(run_id="r-ac6")
    # §1i: pre-stage a warm cache entry deterministically before invocation.
    mod._SESSION_CACHE["r-ac6:gate"] = ("stale-warm-session", 0)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    assert result.status == "ok", f"AC6: expected ok after retry-then-success; got {result.status!r}"
    assert result.data is not None and result.data.get("retry_attempts") == 2, (
        f"AC6: data['retry_attempts'] must be 2; got {result.data!r}"
    )
    assert len(fake.recorded_calls) == 2
    retry_options = fake.recorded_calls[1]
    assert retry_options.kwargs.get("resume") is None, (
        "AC6: the retry attempt's options must have resume=None (poisoned resume "
        "must not be reused), even with a pre-staged warm cache entry"
    )
    assert mod._SESSION_CACHE.get("r-ac6:gate") == ("sess-retry-ok", 0), (
        f"AC6: cache must be re-populated from the successful retry; got {mod._SESSION_CACHE!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — non-retryable exc: single attempt, zero recorded sleeps
# ---------------------------------------------------------------------------

def test_ac7_non_retryable_no_retry_no_sleep(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"raise": RuntimeError("invalid api key")})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    sleeps = _record_sleeps_direct(monkeypatch)

    mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert len(fake.recorded_calls) == 1, (
        f"AC7: a non-retryable exception must trigger exactly 1 query() call; "
        f"got {len(fake.recorded_calls)}"
    )
    assert sleeps == [], f"AC7: no backoff sleep must be recorded; got {sleeps!r}"


def _record_sleeps_direct(monkeypatch):
    import asyncio

    recorded = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *a, **kw):
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return recorded


# ---------------------------------------------------------------------------
# AC8 — backoff delay formula, bounded + jittered
# ---------------------------------------------------------------------------

def test_ac8_backoff_delay_formula_bounded_and_jittered(monkeypatch):
    _install_fake_agent_sdk(monkeypatch)
    mod = _import_agent_sdk_backend()

    import random as random_mod
    monkeypatch.setattr(random_mod, "random", lambda: 0.25)
    monkeypatch.setenv("HAL_AGENT_SDK_BACKOFF_BASE_SEC", "1.0")
    monkeypatch.setenv("HAL_AGENT_SDK_BACKOFF_CAP_SEC", "8.0")

    delay_fn = getattr(mod, "_backoff_delay_sec", None)
    assert callable(delay_fn), "AC8: _backoff_delay_sec must exist as a module-level callable"

    for n in (0, 1):
        expected = min(8.0, 1.0 * (2 ** n)) * (0.5 + 0.5 * 0.25)
        got = delay_fn(n)
        assert abs(got - expected) < 1e-9, (
            f"AC8: _backoff_delay_sec({n}) expected {expected}; got {got}"
        )
        assert 0 <= got <= 8.0, f"AC8: delay must be bounded by cap; got {got}"


# ---------------------------------------------------------------------------
# AC9 — None-result retries only on retryable-matching stderr
# ---------------------------------------------------------------------------

def test_ac9_none_result_retries_only_when_stderr_matches(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"stderr_lines": ["429 too many requests"]})
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="OK", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sid-9", is_error=False,
        ),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )
    assert len(fake.recorded_calls) >= 2, (
        f"AC9: matching 429 stderr on the None-result sink must trigger a retry; "
        f"got {len(fake.recorded_calls)} calls"
    )
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# AC10 — prod side-effect anchor (§1l): agent_sdk_retry telemetry emitted
# ---------------------------------------------------------------------------

def test_ac10_agent_sdk_retry_telemetry_emitted(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    for _ in range(5):
        fake.behavior_queue.append({
            "raise": RetryableExc("429 rate limit hit", status_code=429)
        })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)
    run_ctx, log = _make_run_ctx(tmp_path, "run-ac10")

    mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root, run_ctx)
    )

    rows = [r for r in log.read_all() if r.get("event_type") == "agent_sdk_retry"]
    assert len(rows) >= 1, (
        f"AC10: expected >=1 'agent_sdk_retry' telemetry row; got {len(rows)} "
        f"(all rows: {[r.get('event_type') for r in log.read_all()]!r})"
    )
    payload = rows[0].get("payload", {})
    assert "attempt" in payload and "reason" in payload, (
        f"AC10: agent_sdk_retry payload must carry attempt+reason; got {payload!r}"
    )
    assert payload.get("classified_retryable") is True, (
        f"AC10: agent_sdk_retry payload must carry classified_retryable=True; got {payload!r}"
    )
    assert "max_attempts" in payload, (
        f"AC10: agent_sdk_retry payload must carry max_attempts; got {payload!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — timeout sink never retried, retry_attempts == 1
# ---------------------------------------------------------------------------

def test_ac11_timeout_never_retried(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({"hang": True})
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=0.05, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC11: expected E_LLM_API_TIMEOUT; got {result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC11: a timeout must trigger exactly 1 query() invocation (never retried); "
        f"got {len(fake.recorded_calls)}"
    )
    assert result.data is not None and result.data.get("retry_attempts") == 1, (
        f"AC11: data['retry_attempts'] must be 1 on the timeout sink; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC12 — GH956 regression guard: salvage wins over retry, no extra attempt
# ---------------------------------------------------------------------------

def test_ac12_salvage_wins_no_retry_after_salvage(monkeypatch, tmp_path):
    fake = _install_fake_agent_sdk(monkeypatch)
    fake.behavior_queue.append({
        "result": ResultMessage(
            result="SALVAGE_OK", usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sess-salvage-retry", is_error=False,
        ),
        "raise": RetryableExc("429 rate limit hit mid-stream", status_code=429),
    })
    mod = _import_agent_sdk_backend()
    root = _repo(tmp_path)

    result = mod.agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="gate.step1", **_common_kwargs(root)
    )

    assert result.status == "ok", (
        f"AC12: a salvageable success must win over retry-on-retryable-exc; "
        f"got {result.status!r} error_code={result.error_code!r}"
    )
    assert len(fake.recorded_calls) == 1, (
        f"AC12: salvage must NOT trigger a retry (extra query() call); got "
        f"{len(fake.recorded_calls)}"
    )
    assert result.data is not None and result.data.get("salvaged_stream_error"), (
        f"AC12: data['salvaged_stream_error'] must be set; got {result.data!r}"
    )
